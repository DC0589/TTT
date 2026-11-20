import json
from unittest.mock import patch

from django.db import DatabaseError, connection
from django.test import RequestFactory, TestCase
from django.urls import reverse

from tracker.ai_interview import GeminiAPIError
from tracker.context_processors import celebration_banners, support_unread
from tracker.models import User
from tracker.views.mock import student_mock_interview_ai
from tracker.views.students import admin_student_detail


class FeatureIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("isolation-admin", is_admin=True)
        cls.student = User.objects.create_user("isolation-student", is_student=True, created_by=cls.admin)

    def request(self, name, user, args=None):
        request = RequestFactory().get(reverse(name, args=args))
        request.user = user
        request.session = {}
        return request

    def test_banner_database_failure_is_logged_and_other_counts_still_work(self):
        def broken_query():
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM missing_banner_table")

        with patch("tracker.context_processors._banner_data", side_effect=broken_query), self.assertLogs(
            "tracker.context_processors", level="ERROR"
        ):
            context = celebration_banners(self.request("admin_dashboard", self.admin))
        self.assertTrue(context["banner_unavailable"])
        self.assertEqual(context["pending_leave_count"], 0)
        self.assertEqual(context["pending_question_count"], 0)
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())

    def test_support_count_failure_shows_warning_without_hiding_main_page(self):
        request = self.request("student_interview_questions", self.student)
        with patch("tracker.services.support.student_has_unread", side_effect=DatabaseError("unavailable")), \
             self.assertLogs("tracker.context_processors", level="ERROR"):
            self.assertTrue(support_unread(request)["support_count_unavailable"])
            from tracker.views.courses import student_interview_questions
            response = student_interview_questions(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"chat notification count", response.content)

    def test_staff_notification_failure_is_explicit_and_other_counts_survive(self):
        request = self.request("admin_dashboard", self.admin)
        with patch("tracker.context_processors.StudentQuestionSubmission.objects.filter",
                   side_effect=DatabaseError("unavailable")), \
             self.assertLogs("tracker.context_processors", level="ERROR"):
            context = celebration_banners(request)
        self.assertTrue(context["question_count_unavailable"])
        self.assertNotIn("pending_question_count", context)
        self.assertEqual(context["pending_leave_count"], 0)
        self.assertIn("placed_companies", context)

    def test_mock_failure_does_not_block_student_profile(self):
        request = self.request("admin_student_detail", self.admin, [self.student.pk])
        with patch("tracker.views.students._mock_progress", side_effect=DatabaseError("unavailable")), \
             self.assertLogs("tracker.views.students", level="ERROR"):
            response = admin_student_detail(request, self.student.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Mock interview progress is temporarily unavailable", response.content)
        self.assertIn(b"isolation-student", response.content)

    def test_student_profile_does_not_start_ai_scoring(self):
        request = self.request("admin_student_detail", self.admin, [self.student.pk])
        with patch("tracker.services.mock_scoring.process_pending") as process:
            response = admin_student_detail(request, self.student.pk)
        self.assertEqual(response.status_code, 200)
        process.assert_not_called()

    @patch("tracker.services.question_sets.generate", side_effect=GeminiAPIError("AI unavailable"))
    def test_ai_failure_returns_error_without_blocking_another_feature(self, generate):
        request = RequestFactory().post(
            reverse("student_mock_interview_ai"),
            json.dumps({"action": "question", "consent": True, "role": "Python"}),
            content_type="application/json",
        )
        request.user = self.student
        response = student_mock_interview_ai(request)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(json.loads(response.content)["error"], "AI unavailable")
        self.assertEqual(admin_student_detail(
            self.request("admin_student_detail", self.admin, [self.student.pk]), self.student.pk
        ).status_code, 200)
