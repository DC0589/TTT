from unittest.mock import patch

from django.test import TestCase, override_settings

from tracker import tasks
from tracker.ai_interview import GeminiAPIError
from tracker.models import Company, PlacedStudent
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
