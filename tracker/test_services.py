from unittest.mock import patch

from django.test import TestCase, override_settings

from tracker import tasks
from tracker.ai_interview import GeminiAPIError
from tracker.models import Company, PlacedStudent, User
from tracker.services.placements import placed_company_names


class CompanyDedupeTests(TestCase):
    def test_variants_share_one_company(self):
        a = PlacedStudent.objects.create(name="A", company="Deloitte")
        b = PlacedStudent.objects.create(name="B", company="deloitte Pvt Ltd")
        self.assertEqual(a.company_ref_id, b.company_ref_id)
        self.assertEqual(Company.objects.count(), 1)

    def test_placed_company_names_unique(self):
        PlacedStudent.objects.create(name="A", company="HCL")
        PlacedStudent.objects.create(name="B", company="hcl")
        self.assertEqual(len(placed_company_names()), 1)


class TaskRunnerTests(TestCase):
    def test_sync_runs_inline_and_swallows_errors(self):
        self.assertEqual(tasks.enqueue(lambda x: x + 1, 1).result(), 2)

        def boom():
            raise RuntimeError("x")

        self.assertIsNone(tasks.enqueue(boom).result())

    @override_settings(BACKGROUND_TASKS="thread")
    def test_thread_mode_returns_result(self):
        self.assertEqual(tasks.enqueue(lambda: 5).result(timeout=5), 5)


class StorageTests(TestCase):
    def test_usage_numbers_and_levels(self):
        from tracker.services import storage

        usage = storage.database_usage()
        self.assertEqual(usage["limit"], 1024 * 1024 * 1024)
        self.assertEqual(usage["free"], usage["limit"] - usage["used"])
        self.assertIn(usage["level"], {"ok", "warning", "critical"})
        mb = 1024 * 1024
        for used, level in ((100 * mb, "ok"), (800 * mb, "warning"), (1000 * mb, "critical")):
            with patch.object(storage, "_sqlite_usage", return_value=(used, [])):
                self.assertEqual(storage.database_usage()["level"], level)

    def test_page_is_admin_only(self):
        from django.urls import reverse

        from tracker.models import User

        admin = User.objects.create_user("sadm", password="pw-12345", is_admin=True)
        hr = User.objects.create_user("shr", password="pw-12345", is_hr=True, created_by=admin)
        student = User.objects.create_user("sstu", password="pw-12345", is_student=True)
        self.client.force_login(admin)
        self.assertContains(self.client.get(reverse("storage_usage")), "Database storage")
        for other in (hr, student):
            self.client.force_login(other)
            self.assertEqual(self.client.get(reverse("storage_usage")).status_code, 403)


class MockQueueTests(TestCase):
    def setUp(self):
        from tracker.models import MockInterviewSession, User

        self.student = User.objects.create_user("mock1", password="pw-12345", is_student=True)
        self.session = MockInterviewSession.objects.create(student=self.student, role="Python")
        self.client.force_login(self.student)

    def submit(self, number):
        import json

        from django.urls import reverse

        return self.client.post(
            reverse("student_mock_interview_ai"),
            json.dumps({
                "action": "feedback", "consent": True, "role": "Python", "lite": True,
                "session_id": self.session.pk, "question_number": number,
                "question": f"Question {number}?", "text": "My answer",
            }),
            content_type="application/json",
        )

    @patch("tracker.services.mock_scoring.generate_json", return_value={"score": 4, "answer_feedback": "Good."})
    def test_answers_are_queued_without_calling_the_ai_then_scored(self, generate):
        from tracker.models import MockInterviewScore
        from tracker.services import mock_scoring

        for number in (3, 1, 2, 10, 5):
            self.assertEqual(self.submit(number).json()["status"], "queued")
        generate.assert_not_called()
        self.assertEqual(self.submit(3).status_code, 409)

        self.assertEqual(mock_scoring.process_pending(max_items=10), 5)
        done = MockInterviewScore.objects.filter(session=self.session, status="complete")
        self.assertEqual(sorted(done.values_list("question_number", flat=True)), [1, 2, 3, 5, 10])
        self.assertFalse(MockInterviewScore.objects.filter(payload__isnull=False).exists())
        self.session.refresh_from_db()
        self.assertEqual(float(self.session.rating), 4.0)

    @override_settings(MOCK_SCORING_PER_MINUTE=2)
    @patch("tracker.services.mock_scoring.generate_json", return_value={"score": 3, "answer_feedback": "ok"})
    def test_scoring_respects_the_per_minute_budget(self, generate):
        from tracker.services import mock_scoring

        for number in range(1, 6):
            self.submit(number)
        self.assertEqual(mock_scoring.process_pending(max_items=10), 2)
        self.assertEqual(mock_scoring.process_pending(max_items=10), 0)
        self.assertEqual(generate.call_count, 2)

    @patch("tracker.services.mock_scoring.generate_json", side_effect=GeminiAPIError("busy"))
    def test_failed_scoring_is_retried_later_then_marked_failed(self, _generate):
        from datetime import timedelta

        from django.utils import timezone

        from tracker.models import MockInterviewScore
        from tracker.services import mock_scoring

        self.submit(1)
        for expected_attempts in range(1, mock_scoring.MAX_ATTEMPTS + 1):
            mock_scoring.process_pending()
            score = MockInterviewScore.objects.get()
            self.assertEqual(score.attempts, expected_attempts)
            if expected_attempts < mock_scoring.MAX_ATTEMPTS:
                self.assertEqual(score.status, "pending")
                self.assertGreater(score.next_attempt_at, timezone.now())
                MockInterviewScore.objects.update(
                    next_attempt_at=timezone.now() - timedelta(seconds=1),
                    last_attempt_at=timezone.now() - timedelta(minutes=5),
                )
        score.refresh_from_db()
        self.assertEqual(score.status, "failed")
        self.assertIsNotNone(score.payload)

        self.assertEqual(mock_scoring.retry_failed(self.session), 1)
        score.refresh_from_db()
        self.assertEqual((score.status, score.attempts), ("pending", 0))

    @override_settings(MOCK_BANK_MIN_SETS=2)
    def test_question_bank_is_served_once_it_has_enough_unseen_sets(self):
        import json

        from django.urls import reverse

        from tracker.models import MockQuestionSet

        questions = [{"text": f"Q{n}?", "type": "concept"} for n in range(10)]
        for n in range(3):
            MockQuestionSet.objects.create(
                topic="Python", difficulty="medium",
                questions=[{**q, "text": f"{q['text']} v{n}"} for q in questions],
            )
        served = set()
        with patch("tracker.services.question_sets.generate_json") as generate:
            for _ in range(2):
                response = self.client.post(
                    reverse("student_mock_interview_ai"),
                    json.dumps({"action": "question", "consent": True, "role": "Python", "difficulty": "medium"}),
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 200)
                served.add(response.json()["questions"][0]["text"])
            generate.assert_not_called()
        self.assertEqual(len(served), 2)

    @patch("tracker.services.question_sets.generate_json")
    def test_generated_questions_are_stored_for_reuse(self, generate):
        import json

        from django.urls import reverse

        from tracker.models import MockQuestionSet

        generate.return_value = {"questions": [{"text": f"Q{n}?", "type": "concept"} for n in range(10)]}
        response = self.client.post(
            reverse("student_mock_interview_ai"),
            json.dumps({"action": "question", "consent": True, "role": "Python", "difficulty": "medium"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MockQuestionSet.objects.filter(topic="Python").count(), 1)


@override_settings(CRON_SECRET="cron-test-secret")
class MockScoringCronTests(TestCase):
    def test_requires_the_secret(self):
        from django.urls import reverse

        url = reverse("cron_score_mock")
        self.assertEqual(self.client.get(url).status_code, 403)
        ok = self.client.get(url, headers={"Authorization": "Bearer cron-test-secret"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json(), {"scored": 0})


class MockSessionFlowTests(TestCase):
    def setUp(self):
        from tracker.models import MockInterviewSession, User

        self.student = User.objects.create_user("flow1", password="pw-12345", is_student=True)
        self.other = User.objects.create_user("flow2", password="pw-12345", is_student=True)
        self.session = MockInterviewSession.objects.create(student=self.student, role="Python")
        self.client.force_login(self.student)

    def call(self, **payload):
        import json

        from django.urls import reverse

        return self.client.post(
            reverse("student_mock_interview_ai"),
            json.dumps({"consent": True, "session_id": self.session.pk, **payload}),
            content_type="application/json",
        )

    def test_ending_before_ten_questions_requires_a_reason(self):
        self.assertEqual(self.call(action="finish", expected_answers=4).status_code, 400)
        self.assertEqual(self.call(action="finish", expected_answers=4, end_reason="  ok ").status_code, 400)
        self.session.refresh_from_db()
        self.assertIsNone(self.session.completed_at)
        response = self.call(
            action="finish", expected_answers=4, end_reason="Internet or connection problem: wifi dropped"
        )
        self.assertEqual(response.status_code, 200)
        self.session.refresh_from_db()
        self.assertIsNotNone(self.session.completed_at)
        self.assertIn("wifi dropped", self.session.end_reason)

    def test_finishing_all_ten_needs_no_reason(self):
        self.assertEqual(self.call(action="finish", expected_answers=10).status_code, 200)
        self.session.refresh_from_db()
        self.assertEqual(self.session.end_reason, "")

    def test_end_reason_is_shown_to_the_admin(self):
        from django.urls import reverse

        from tracker.models import User

        self.call(action="finish", expected_answers=3, end_reason="I ran out of time: had to leave")
        admin = User.objects.create_user("flowadmin", password="pw-12345", is_admin=True)
        self.student.created_by = admin
        self.student.save()
        self.client.force_login(admin)
        page = self.client.get(reverse("admin_mock_interviews"))
        self.assertContains(page, "I ran out of time: had to leave")
        self.assertContains(self.client.get(reverse("admin_student_detail", args=[self.student.pk])), "Ended early")

    @patch("tracker.services.mock_scoring.generate_json", return_value={"score": 5, "answer_feedback": "Great."})
    def test_drain_scores_queued_answers_from_any_session_and_results_finish_them(self, _generate):


        from tracker.models import MockInterviewScore, MockInterviewSession

        mine = MockInterviewSession.objects.create(student=self.other, role="SQL")
        MockInterviewScore.objects.create(
            session=mine, question_number=1, question="Q?", status="pending",
            payload={"parts": [{"text": "x"}], "audio": False, "lite": True},
        )
        self.assertEqual(self.call(action="drain").json()["scored"], 1)
        self.assertEqual(MockInterviewScore.objects.get(session=mine).status, "complete")
        # The results poll also drives scoring for the session being viewed.
        self.session.expected_answers = 1
        self.session.save()
        MockInterviewScore.objects.create(
            session=self.session, question_number=1, question="Q?", status="pending",
            payload={"parts": [{"text": "x"}], "audio": False, "lite": True},
        )
        results = self.call(action="results").json()
        self.assertTrue(results["ready"])
        self.assertEqual(results["retryable"], 0)

    def test_student_cannot_retry_or_finish_someone_elses_session(self):
        self.client.force_login(self.other)
        self.assertEqual(self.call(action="retry").status_code, 404)
        self.assertEqual(self.call(action="finish", expected_answers=10).status_code, 404)

    @override_settings(MOCK_SCORING_PER_MINUTE=12)
    def test_a_claimed_answer_is_not_scored_twice(self):
        from tracker.models import MockInterviewScore
        from tracker.services import mock_scoring

        score = MockInterviewScore.objects.create(
            session=self.session, question_number=1, question="Q?", status="pending",
            payload={"parts": [{"text": "x"}], "audio": False, "lite": True},
        )
        self.assertTrue(mock_scoring._claim(score))
        self.assertFalse(mock_scoring._claim(score))


class MockModeTests(MockSessionFlowTests):
    test_ending_before_ten_questions_requires_a_reason = None
    test_finishing_all_ten_needs_no_reason = None
    test_end_reason_is_shown_to_the_admin = None
    test_drain_scores_queued_answers_from_any_session_and_results_finish_them = None
    test_student_cannot_retry_or_finish_someone_elses_session = None
    test_a_claimed_answer_is_not_scored_twice = None

    @patch("tracker.views.mock.question_sets.serve")
    def test_session_records_mode_and_reason(self, serve):
        from tracker.models import MockInterviewSession, MockQuestionSet

        serve.return_value = MockQuestionSet.objects.create(
            topic="Python", difficulty="medium", questions=[{"question": "Q?"}] * 10
        )
        response = self.call(action="question", role="Python", lite=True, mode_reason="3g, measured 0.4 Mbps")
        session = MockInterviewSession.objects.get(pk=response.json()["session_id"])
        self.assertEqual((session.mode, session.mode_reason), ("practice", "3g, measured 0.4 Mbps"))
        response = self.call(action="question", role="Python")
        self.assertEqual(MockInterviewSession.objects.get(pk=response.json()["session_id"]).mode, "full")


class AnnouncementTests(TestCase):
    def setUp(self):
        from tracker.models import User

        self.admin = User.objects.create_user("annadmin", password="pw-12345", is_admin=True)
        self.student = User.objects.create_user("annstudent", password="pw-12345", is_student=True)

    def make(self, title, start_hours, end_hours, **extra):
        from datetime import timedelta

        from django.utils import timezone

        from tracker.models import Announcement

        now = timezone.now()
        return Announcement.objects.create(
            title=title, message="Hello all", start_at=now + timedelta(hours=start_hours),
            end_at=now + timedelta(hours=end_hours), **extra,
        )

    def test_only_live_announcements_for_the_right_audience_are_shown(self):
        from django.urls import reverse

        self.make("Live now", -1, 5)
        self.make("Too early", 2, 5)
        self.make("Expired", -5, -1)
        self.make("Paused", -1, 5, is_active=False)
        self.make("Staff only", -1, 5, audience="staff")
        self.client.force_login(self.student)
        page = self.client.get(reverse("student_dashboard"))
        self.assertContains(page, "Live now")
        for hidden in ("Too early", "Expired", "Paused", "Staff only"):
            self.assertNotContains(page, hidden)
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("admin_dashboard")), "Staff only")

    def test_popup_shows_once_per_login(self):
        from django.urls import reverse

        self.make("Live now", -1, 5)
        self.client.force_login(self.student)
        url = reverse("student_dashboard")
        self.assertContains(self.client.get(url), "Live now")
        self.assertNotContains(self.client.get(url), "Live now")
        self.client.logout()
        self.client.force_login(self.student)
        self.assertContains(self.client.get(url), "Live now")

    def test_admin_can_create_validate_and_delete(self):
        from django.urls import reverse

        from tracker.models import Announcement

        self.client.force_login(self.admin)
        url = reverse("admin_announcement_add")
        bad = self.client.post(url, {
            "title": "T", "message": "M", "audience": "all",
            "start_at": "2026-10-20T10:00", "end_at": "2026-10-19T10:00", "is_active": "on",
        })
        self.assertContains(bad, "The end must be after the start.")
        ok = self.client.post(url, {
            "title": "Holiday", "message": "Closed on Friday", "audience": "all",
            "start_at": "2026-10-20T10:00", "end_at": "2026-10-25T10:00", "is_active": "on",
        })
        self.assertRedirects(ok, reverse("admin_announcements"))
        item = Announcement.objects.get(title="Holiday")
        self.assertEqual(item.created_by, self.admin)
        self.assertContains(self.client.get(reverse("admin_announcements")), "Holiday")
        self.client.post(reverse("admin_announcement_delete", args=[item.pk]))
        self.assertFalse(Announcement.objects.exists())

    def test_hr_can_manage_and_sees_popup(self):
        from django.urls import reverse

        from tracker.models import User

        hr = User.objects.create_user("annhr", password="pw-12345", is_hr=True)
        self.make("For staff", -1, 5, audience="staff")
        self.client.force_login(hr)
        self.assertEqual(self.client.get(reverse("admin_announcements")).status_code, 200)
        self.assertContains(self.client.get(reverse("hr_students")), "For staff")

    def test_students_cannot_manage_announcements(self):
        from django.urls import reverse

        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("admin_announcements")).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin_announcement_add"), {}).status_code, 403)


class PlacedCompanyFilterTests(TestCase):
    def test_clicking_a_company_filters_the_list(self):
        from django.urls import reverse

        from tracker.models import Company, PlacedStudent, User

        admin = User.objects.create_user("pfadmin", password="pw-12345", is_admin=True)
        PlacedStudent.objects.create(name="Asha Rao", company="LTIMindtree", year=2026)
        PlacedStudent.objects.create(name="Ravi Kumar", company="Deloitte", year=2026)
        self.client.force_login(admin)
        ltim = Company.objects.get(name="LTIMindtree")
        page = self.client.get(reverse("admin_placed_students"), {"company": ltim.pk})
        self.assertContains(page, "Asha Rao")
        self.assertNotContains(page, "Ravi Kumar")
        self.assertContains(self.client.get(reverse("admin_placed_students")), "Ravi Kumar")


class SharedAdminDataTests(TestCase):
    def test_partner_admin_sees_and_manages_the_same_data(self):
        from django.urls import reverse

        from tracker.models import Group, User

        main = User.objects.create_user("mainadmin", password="pw-12345", is_admin=True)
        partner = User.objects.create_user(
            "partneradmin", password="pw-12345", is_admin=True, shares_data_with=main
        )
        batch = Group.objects.create(name="Shared batch", admin=main)
        student = User.objects.create_user("sharedstud", password="pw-12345", is_student=True, created_by=main)
        self.client.force_login(partner)
        self.assertContains(self.client.get(reverse("admin_groups")), "Shared batch")
        self.assertContains(self.client.get(reverse("admin_students")), student.username)
        self.assertEqual(partner.data_owner, main)
        self.assertEqual(set(partner.team_ids), {main.pk, partner.pk})
        self.assertEqual(
            self.client.get(reverse("admin_group_detail", args=[batch.pk])).status_code, 200
        )


class MockFocusAreaTests(TestCase):
    def test_clean_focus_only_accepts_areas_of_the_topic(self):
        from tracker.mock_topics import MOCK_TOPICS, clean_focus

        self.assertEqual(clean_focus("Python", "recursion"), "recursion")
        self.assertEqual(clean_focus("Python", "joins"), "")
        self.assertEqual(clean_focus("Custom role", "recursion"), "")
        self.assertEqual(clean_focus("Python", None), "")
        for topic, info in MOCK_TOPICS.items():
            self.assertTrue(info["areas"], topic)

    def test_prompt_is_limited_to_the_chosen_focus(self):
        from tracker.services import question_sets

        focused = question_sets.build_prompt("Python", "medium", focus="recursion")
        self.assertIn("specifically about 'recursion'", focused)
        self.assertNotIn("Draw from these focus areas", focused)
        overall = question_sets.build_prompt("Python", "medium")
        self.assertIn("Draw from these focus areas", overall)

    def test_stored_sets_are_kept_per_focus(self):
        from tracker.models import MockQuestionSet
        from tracker.services import question_sets

        user = User.objects.create_user("focus-student", "f@example.com", "pw12345!", is_student=True)
        questions = [{"text": f"Q{n}?", "type": "concept"} for n in range(10)]
        question_sets.store("Python", "medium", questions, "recursion")
        question_sets.store("Python", "medium", questions, "")
        self.assertEqual(MockQuestionSet.objects.filter(topic="Python").count(), 2)
        with override_settings(MOCK_BANK_MIN_SETS=1):
            self.assertEqual(question_sets.serve(user, "Python", "medium", "recursion").focus, "recursion")
            self.assertEqual(question_sets.serve(user, "Python", "medium").focus, "")
            self.assertIsNone(question_sets.serve(user, "Python", "medium", "generators and iterators"))
