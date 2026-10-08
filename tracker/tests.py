from datetime import date, timedelta
import base64
import json
import re
from io import BytesIO
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from django.contrib.auth.hashers import check_password
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Group, GroupMembership, Interview, InterviewRound, InterviewStatus,
    LearningCourse, MockInterviewScore, MockInterviewSession,
    StudentRegistrationRequest, User,
)
from .ai_interview import GeminiAPIError, generate_json


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("admin", password="pw12345!", is_admin=True)
        cls.alice = User.objects.create_user("alice", password="pw12345!", is_student=True)
        cls.bob = User.objects.create_user("bob", password="pw12345!", is_student=True)
        cls.group = Group.objects.create(name="Batch A", admin=cls.admin)
        for s in (cls.alice, cls.bob):
            GroupMembership.objects.create(group=cls.group, student=s)
        cls.iv = Interview.objects.create(student=cls.alice, group=cls.group, company_name="Acme",
                                          role="Dev", date_of_interview=date.today())


class ModelTests(Base):
    def test_default_status_and_badge(self):
        self.assertEqual(self.iv.final_status, "in-progress")
        InterviewStatus.objects.create(interview=self.iv, final_status="selected")
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.final_label, "Selected")

    def test_duplicate_membership_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            GroupMembership.objects.create(group=self.group, student=self.alice)

    def test_progress(self):
        InterviewRound.objects.create(interview=self.iv, round_number=1, description="x", status="cleared")
        InterviewRound.objects.create(interview=self.iv, round_number=2, description="y")
        self.assertEqual(self.iv.progress, "1/2 cleared")

    def test_superuser_is_admin(self):
        self.assertTrue(User.objects.create_superuser("root", password="x").is_admin)


PROFILE = {
    "mobile_number": "9876543210", "graduation": "B.Tech", "department": "CSE",
    "hometown": "Hyderabad", "parent_name": "Parent", "parent_mobile_number": "9876543211",
    "skills": "Python",
}


class AuthTests(Base):
    def test_login_redirects_by_role(self):
        r = self.client.post(reverse("login"), {"username": "admin", "password": "pw12345!"})
        self.assertRedirects(r, reverse("admin_dashboard"))
        self.client.logout()
        r = self.client.post(reverse("login"), {"username": "alice", "password": "pw12345!"})
        self.assertRedirects(r, reverse("student_dashboard"))

    def test_hr_login_redirects_to_hr_workspace(self):
        hr = User.objects.create_user(
            "login-hr", password="pw12345!", is_hr=True, created_by=self.admin
        )

        response = self.client.post(reverse("login"), {
            "username": hr.username,
            "password": "pw12345!",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("hr_students"))

    def test_external_registration_requires_verification_and_admin_approval(self):
        self.admin.email = "admin@example.com"
        self.admin.save(update_fields=["email"])
        r = self.client.post(reverse("register"), {**PROFILE,
            "username": "dave", "email": "d@example.com",
            "password1": "S7rong-pass-99", "password2": "S7rong-pass-99"})
        registration = StudentRegistrationRequest.objects.get(username="dave")
        self.assertRedirects(r, reverse("verify_registration", args=[registration.pk]))
        self.assertFalse(User.objects.filter(username="dave").exists())
        self.assertEqual(registration.status, StudentRegistrationRequest.AWAITING_VERIFICATION)
        self.assertTrue(check_password("S7rong-pass-99", registration.password_hash))

        code = re.search(
            r"(?m)^Your email verification code is: (\d{6})$",
            mail.outbox[0].body,
        )
        self.assertIsNotNone(code)
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        self.assertEqual(mail.outbox[0].alternatives[0][1], "text/html")
        self.assertIn(code.group(1), mail.outbox[0].alternatives[0][0])
        self.assertIn("Tweak Talent", mail.outbox[0].alternatives[0][0])
        self.assertIn("Hello dave,", mail.outbox[0].body)
        verify_path = reverse("verify_registration", args=[registration.pk])
        verify_page = self.client.get(verify_path)
        self.assertEqual(verify_page.status_code, 200)
        self.assertContains(verify_page, 'name="code"')
        verify_response = self.client.post(verify_path, {"code": code.group(1)})
        self.assertRedirects(
            verify_response,
            f"{reverse('registration_submitted')}?verified=1",
        )
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.AWAITING_APPROVAL)
        self.assertFalse(User.objects.filter(username="dave").exists())
        self.assertEqual(len(mail.outbox), 2)
        self.assertIn("admin@example.com", mail.outbox[1].to)
        self.assertIn("Review signup request", mail.outbox[1].alternatives[0][0])
        replay = self.client.post(verify_path, {"code": code.group(1)})
        self.assertRedirects(replay, reverse("login"))
        self.assertEqual(len(mail.outbox), 2)

        self.client.force_login(self.admin)
        approve_url = reverse("registration_approve", args=[registration.pk])
        response = self.client.post(approve_url)
        self.assertRedirects(response, reverse("admin_registrations"))
        self.assertEqual(len(mail.outbox), 3)
        self.assertIn("Hello dave,", mail.outbox[2].body)
        self.assertIn("Your student account is approved", mail.outbox[2].alternatives[0][0])
        student = User.objects.get(username="dave")
        self.assertTrue(student.is_student)
        self.assertEqual(student.created_by, self.admin)
        self.assertTrue(check_password("S7rong-pass-99", student.password))
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.APPROVED)
        self.assertEqual(registration.password_hash, "")
        self.assertTrue(student.check_password("S7rong-pass-99"))


    def test_rejection_notification_includes_branded_html_email(self):
        registration = StudentRegistrationRequest.objects.create(
            username="rejected-student",
            email="rejected@example.com",
            password_hash="unused",
            verification_code_hash="",
            verification_expires_at=timezone.now(),
            status=StudentRegistrationRequest.AWAITING_APPROVAL,
            verified_at=timezone.now(),
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("registration_reject", args=[registration.pk])
        )

        self.assertRedirects(response, reverse("admin_registrations"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["rejected@example.com"])
        self.assertIn("Hello rejected-student,", mail.outbox[0].body)
        self.assertIn(
            "unable to approve the account",
            mail.outbox[0].alternatives[0][0],
        )

    def test_unverified_request_cannot_be_approved(self):
        self.client.post(reverse("register"), {**PROFILE,
            "username": "eve", "email": "eve@example.com",
            "password1": "S7rong-pass-99", "password2": "S7rong-pass-99"})
        registration = StudentRegistrationRequest.objects.get(username="eve")
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("registration_approve", args=[registration.pk]))

        self.assertRedirects(response, reverse("admin_registrations"))
        self.assertFalse(User.objects.filter(username="eve").exists())
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.AWAITING_VERIFICATION)

    def test_registration_otp_rejects_incorrect_codes_and_locks_after_five_attempts(self):
        self.client.post(reverse("register"), {**PROFILE,
            "username": "otp-student",
            "email": "otp@example.com",
            "password1": "S7rong-pass-99",
            "password2": "S7rong-pass-99",
        })
        registration = StudentRegistrationRequest.objects.get(username="otp-student")
        verify_url = reverse("verify_registration", args=[registration.pk])
        code_match = re.search(
            r"(?m)^Your email verification code is: (\d{6})$",
            mail.outbox[-1].body,
        )
        self.assertIsNotNone(code_match)
        wrong_code = "000000" if code_match.group(1) != "000000" else "000001"

        for attempt in range(4):
            response = self.client.post(verify_url, {"code": wrong_code})
            self.assertEqual(response.status_code, 200, attempt)
            registration.refresh_from_db()
            self.assertEqual(registration.verification_attempts, attempt + 1)

        response = self.client.post(verify_url, {"code": wrong_code})
        self.assertRedirects(response, reverse("register"))
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.EXPIRED)
        self.assertEqual(registration.password_hash, "")
        self.assertEqual(registration.verification_code_hash, "")

    def test_registration_otp_can_be_resent_and_rotates_the_code(self):
        registration = StudentRegistrationRequest.objects.create(
            username="resend-student",
            email="resend@example.com",
            password_hash="stored-password-hash",
            verification_code_hash="a" * 64,
            verification_attempts=4,
            verification_last_sent_at=timezone.now() - timedelta(seconds=61),
            verification_expires_at=timezone.now() + timedelta(minutes=5),
        )
        old_code_hash = registration.verification_code_hash

        with patch("tracker.views.render_to_string", return_value="<p>Verify code</p>"):
            response = self.client.post(
                reverse("resend_registration_otp", args=[registration.pk])
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("verify_registration", args=[registration.pk]))
        self.assertEqual(len(mail.outbox), 1)
        resent_code = re.search(
            r"(?m)^Your email verification code is: (\d{6})$",
            mail.outbox[0].body,
        )
        self.assertIsNotNone(resent_code)
        registration.refresh_from_db()
        self.assertNotEqual(registration.verification_code_hash, old_code_hash)
        self.assertEqual(registration.verification_attempts, 0)
        self.assertEqual(registration.verification_resend_count, 1)
        self.assertGreater(
            registration.verification_expires_at,
            timezone.now() + timedelta(minutes=9),
        )

    def test_registration_otp_resend_enforces_cooldown_and_limit(self):
        registration = StudentRegistrationRequest.objects.create(
            username="limited-resend",
            email="limited-resend@example.com",
            password_hash="stored-password-hash",
            verification_code_hash="b" * 64,
            verification_last_sent_at=timezone.now(),
            verification_expires_at=timezone.now() + timedelta(minutes=5),
        )

        cooldown_response = self.client.post(
            reverse("resend_registration_otp", args=[registration.pk])
        )
        self.assertEqual(cooldown_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)

        registration.verification_last_sent_at = timezone.now() - timedelta(seconds=61)
        registration.verification_resend_count = 3
        registration.save(update_fields=["verification_last_sent_at", "verification_resend_count"])
        limited_response = self.client.post(
            reverse("resend_registration_otp", args=[registration.pk])
        )
        self.assertEqual(limited_response.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)
        registration.refresh_from_db()
        self.assertEqual(registration.verification_resend_count, 3)

    def test_admin_created_student_does_not_need_email_verification(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin_student_add"), {
            "username": "direct-student",
            "email": "direct@example.com",
            "referred_by": "A. Referrer",
            "mobile_number": "+1 555 0100",
            "graduation": "BSc Computer Science, 2025",
            "department": "Computer Science",
            "hometown": "Springfield",
            "parent_name": "Parent Example",
            "parent_mobile_number": "+1 555 0101",
            "skills": "Python, Django",
            "password1": "T7rong-pass-99",
            "password2": "T7rong-pass-99",
        })

        student = User.objects.get(username="direct-student")
        self.assertRedirects(
            response, reverse("admin_student_detail", args=[student.pk]))
        self.assertTrue(student.is_student)
        self.assertEqual(student.created_by, self.admin)
        self.assertEqual(student.referred_by, "A. Referrer")
        self.assertEqual(student.mobile_number, "+1 555 0100")
        self.assertEqual(student.graduation, "BSc Computer Science, 2025")
        self.assertEqual(student.department, "Computer Science")
        self.assertEqual(student.hometown, "Springfield")
        self.assertEqual(student.parent_name, "Parent Example")
        self.assertEqual(student.parent_mobile_number, "+1 555 0101")
        self.assertEqual(student.skills, "Python, Django")
        self.assertFalse(StudentRegistrationRequest.objects.filter(
            username="direct-student").exists())

    def test_expired_registration_can_be_retried(self):
        self.client.post(reverse("register"), {**PROFILE,
            "username": "expired-student",
            "email": "expired@example.com",
            "password1": "T7rong-pass-99",
            "password2": "T7rong-pass-99",
        })
        registration = StudentRegistrationRequest.objects.get(username="expired-student")
        registration.verification_expires_at = timezone.now() - timedelta(minutes=1)
        registration.save(update_fields=["verification_expires_at"])

        response = self.client.post(reverse("register"), {**PROFILE,
            "username": "expired-student",
            "email": "expired@example.com",
            "password1": "T7rong-pass-99",
            "password2": "T7rong-pass-99",
        })

        retried = StudentRegistrationRequest.objects.get(
            username="expired-student",
            status=StudentRegistrationRequest.AWAITING_VERIFICATION,
        )
        self.assertRedirects(
            response, reverse("verify_registration", args=[retried.pk])
        )
        registration.refresh_from_db()
        self.assertEqual(registration.status, StudentRegistrationRequest.EXPIRED)
        self.assertEqual(registration.password_hash, "")
        self.assertEqual(registration.verification_code_hash, "")
        self.assertEqual(
            StudentRegistrationRequest.objects.filter(username="expired-student").count(),
            2,
        )

    def test_anonymous_redirected(self):
        r = self.client.get(reverse("student_dashboard"))
        self.assertEqual(r.status_code, 302)


class MockInterviewTests(Base):
    def test_student_can_open_mock_interview(self):
        self.client.force_login(self.alice)
        response = self.client.get(reverse("student_mock_interview"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Mock Interview")

    def test_admin_cannot_open_student_mock_interview(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("student_mock_interview"))
        self.assertEqual(response.status_code, 403)

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-test",
        GEMINI_PRIMARY_MODEL="gemini-test",
        GEMINI_FALLBACK_MODEL="gemini-lite-test",
    )
    @patch("tracker.ai_interview.urlopen")
    def test_gemini_503_is_retried(self, mock_urlopen):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"question": "Tell me about your experience."}'}]}}]
        }).encode()
        mock_urlopen.side_effect = [
            HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO()),
            response,
        ]

        with patch("tracker.ai_interview.time.sleep") as mock_sleep:
            result = generate_json([{"text": "Ask a question."}])

        self.assertEqual(result["question"], "Tell me about your experience.")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertGreaterEqual(mock_sleep.call_args.args[0], 0.25)
        self.assertLess(mock_sleep.call_args.args[0], 0.5)

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-test",
        GEMINI_PRIMARY_MODEL="gemini-test",
        GEMINI_FALLBACK_MODEL="gemini-lite-test",
    )
    @patch("tracker.ai_interview.urlopen")
    def test_gemini_persistent_503_returns_retry_message(self, mock_urlopen):
        mock_urlopen.side_effect = [
            HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO())
            for _ in range(3)
        ]

        with patch("tracker.ai_interview.time.sleep"):
            with self.assertRaisesMessage(
                GeminiAPIError,
                "The AI service is unavailable right now. Please try again shortly.",
            ):
                generate_json([{"text": "Ask a question."}])

        self.assertEqual(mock_urlopen.call_count, 3)

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-test",
        GEMINI_PRIMARY_MODEL="gemini-test",
        GEMINI_FALLBACK_MODEL="gemini-lite-test",
    )
    @patch("tracker.ai_interview.urlopen")
    def test_gemini_503_falls_back_to_flash_lite(self, mock_urlopen):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"question":"Tell me about your experience."}'}]}}]
        }).encode()
        mock_urlopen.side_effect = [
            HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO()),
            HTTPError("https://example.test", 503, "Unavailable", {}, BytesIO()),
            response,
        ]

        with patch("tracker.ai_interview.time.sleep"):
            result = generate_json([{"text": "Ask a question."}])

        self.assertEqual(result["question"], "Tell me about your experience.")
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertIn("models/gemini-test:generateContent", mock_urlopen.call_args_list[0].args[0].full_url)
        self.assertIn("models/gemini-lite-test:generateContent", mock_urlopen.call_args_list[-1].args[0].full_url)

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-test",
        GEMINI_PRIMARY_MODEL="gemini-test",
        GEMINI_FALLBACK_MODEL="gemini-lite-test",
    )
    @patch("tracker.ai_interview.urlopen")
    def test_gemini_connection_error_is_retried(self, mock_urlopen):
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": '{"question":"Tell me about your experience."}'}]}}]
        }).encode()
        mock_urlopen.side_effect = [URLError("connection reset"), response]

        with patch("tracker.ai_interview.time.sleep") as mock_sleep:
            result = generate_json([{"text": "Ask a question."}])

        self.assertEqual(result["question"], "Tell me about your experience.")
        self.assertEqual(mock_urlopen.call_count, 2)
        self.assertGreaterEqual(mock_sleep.call_args.args[0], 0.25)
        self.assertLess(mock_sleep.call_args.args[0], 0.5)

    @override_settings(
        GEMINI_API_KEY="test-key",
        GEMINI_MODEL="gemini-test",
        GEMINI_PRIMARY_MODEL="gemini-test",
        GEMINI_FALLBACK_MODEL="gemini-lite-test",
    )
    @patch("tracker.ai_interview.urlopen")
    def test_audio_feedback_is_sent_and_rating_is_saved(self, mock_urlopen):
        self.client.force_login(self.alice)
        session = MockInterviewSession.objects.create(
            student=self.alice, role="Data analyst"
        )
        finish_response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "finish",
            "consent": True,
            "session_id": session.pk,
            "expected_answers": 1,
        }, content_type="application/json")
        self.assertEqual(finish_response.status_code, 200)
        response_body = {
            "candidates": [{"content": {"parts": [{"text": json.dumps({
                "score": 4,
                "answer_transcript": "I check ranges and missing values.",
                "answer_feedback": "Clear answer.",
                "camera_feedback": "Good framing.",
                "screen_feedback": "Readable screen.",
                "next_question": "What would you improve next?",
            })}]}}]
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps(response_body).encode()

        mock_urlopen.return_value = FakeResponse()
        jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xfftest").decode()
        audio = "data:audio/webm;codecs=opus;base64," + base64.b64encode(b"webm-audio").decode()
        response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "feedback",
            "consent": True,
            "role": "Data analyst",
            "session_id": session.pk,
            "question_number": 1,
            "question": "How do you validate data?",
            "audio": audio,
            "frames": {"camera": jpeg, "screen": jpeg},
        }, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "complete")
        session.refresh_from_db()
        self.assertEqual(str(session.rating), "4.00")
        self.assertEqual(session.expected_answers, 1)
        self.assertIsNotNone(session.completed_at)
        self.assertEqual(session.scores.get().score, 4)
        saved_score = session.scores.get()
        self.assertEqual(saved_score.answer_feedback, "Clear answer.")
        self.assertEqual(saved_score.camera_feedback, "Good framing.")
        self.assertEqual(saved_score.screen_feedback, "Readable screen.")
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual(
            payload["generationConfig"]["thinkingConfig"]["thinkingLevel"],
            "LOW",
        )
        parts = payload["contents"][0]["parts"]
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "audio/webm")
        self.assertEqual(parts[1]["inlineData"]["data"], audio.split(",", 1)[1])
        self.assertEqual(sum("inlineData" in part for part in parts), 3)

        results_response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "results",
            "consent": True,
            "session_id": session.pk,
        }, content_type="application/json")
        self.assertTrue(results_response.json()["ready"])
        self.assertEqual(results_response.json()["session_rating"], 4.0)
        self.assertEqual(results_response.json()["scores"][0]["question"], "How do you validate data?")

    def test_results_poll_reports_pending_answers(self):
        self.client.force_login(self.alice)
        session = MockInterviewSession.objects.create(
            student=self.alice, role="Data analyst", expected_answers=2
        )
        MockInterviewScore.objects.create(
            session=session,
            question_number=1,
            question="How do you validate data?",
            status=MockInterviewScore.PENDING,
        )

        response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "results",
            "consent": True,
            "session_id": session.pk,
        }, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ready"])
        self.assertEqual(response.json()["pending_count"], 2)

        finish_response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "finish",
            "consent": True,
            "session_id": session.pk,
            "expected_answers": 1,
        }, content_type="application/json")

        self.assertEqual(finish_response.status_code, 200)
        session.refresh_from_db()
        self.assertEqual(session.expected_answers, 2)

    def test_student_cannot_submit_audio_to_another_students_session(self):
        session = MockInterviewSession.objects.create(
            student=self.bob, role="Private role"
        )
        self.client.force_login(self.alice)

        response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "feedback",
            "consent": True,
            "role": "Developer",
            "session_id": session.pk,
            "question_number": 1,
        }, content_type="application/json")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(MockInterviewScore.objects.count(), 0)

    @override_settings(GEMINI_API_KEY="test-key", GEMINI_MODEL="gemini-test")
    @patch("tracker.ai_interview.urlopen")
    def test_first_question_creates_a_student_owned_session(self, mock_urlopen):
        self.client.force_login(self.alice)

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({
                    "candidates": [{"content": {"parts": [{"text": json.dumps({
                        "questions": [f"Question {number}?" for number in range(1, 11)],
                    })}]}}]
                }).encode()

        mock_urlopen.return_value = FakeResponse()
        response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "question",
            "consent": True,
            "role": "Data analyst",
            "question_number": 1,
        }, content_type="application/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["questions"]), 10)
        session = MockInterviewSession.objects.get(pk=response.json()["session_id"])
        self.assertEqual(session.student, self.alice)
        self.assertEqual(session.role, "Data analyst")

    def test_mock_interview_api_rejects_missing_consent(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("student_mock_interview_ai"), {
            "action": "question", "role": "Developer", "question_number": 1,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 400)


class EmailCommandTests(TestCase):
    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="",
        EMAIL_HOST_USER="",
        EMAIL_HOST_PASSWORD="",
        DEFAULT_FROM_EMAIL="",
    )
    def test_email_command_reports_missing_smtp_configuration(self):
        with self.assertRaisesMessage(CommandError, "EMAIL_HOST"):
            call_command("test_email", to="student@example.com")


class PermissionTests(Base):
    def test_student_cannot_open_admin_pages(self):
        self.client.force_login(self.alice)
        for name in (
            "admin_dashboard", "admin_groups", "admin_students",
            "admin_mock_interviews",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin_group_detail", args=[self.group.pk])).status_code, 403)
        self.assertEqual(self.client.get(
            reverse("admin_student_detail", args=[self.alice.pk])).status_code, 403)

    def test_admin_cannot_open_student_pages(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("student_dashboard")).status_code, 403)

    def test_hr_cannot_open_admin_or_mock_review_pages(self):
        hr = User.objects.create_user(
            "hr-staff", password="pw12345!", is_hr=True, created_by=self.admin
        )
        self.client.force_login(hr)
        for name in (
            "admin_dashboard", "admin_reports",
            "admin_hr_user_add",
        ):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.assertEqual(self.client.get(reverse("admin_groups")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin_group_detail", args=[self.group.pk])).status_code,
            200,
        )
        self.assertEqual(self.client.get(reverse("admin_group_add")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin_group_edit", args=[self.group.pk])).status_code,
            403,
        )
        request = RequestFactory().get(reverse("hr_students"))
        request.user = hr
        with patch("tracker.views.render") as mock_render:
            from .views import hr_students

            hr_students(request)
        self.assertEqual(mock_render.call_args.args[1], "tracker/hr/students.html")

    def test_student_cannot_see_others_interview(self):
        self.client.force_login(self.bob)
        url = reverse("student_interview_detail", args=[self.iv.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse("round_add", args=[self.iv.pk]),
                                          {"description": "hack"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("interview_delete", args=[self.iv.pk])).status_code, 404)

    def test_other_admin_cannot_open_group(self):
        other = User.objects.create_user("admin2", password="pw12345!", is_admin=True)
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("admin_group_detail", args=[self.group.pk])).status_code, 404)


class ViewTests(Base):
    def test_admin_mock_reviews_filter_batch_student_topic_and_date(self):
        recent_python = MockInterviewSession.objects.create(
            student=self.alice, role="Python", rating="4.00"
        )
        older_python = MockInterviewSession.objects.create(
            student=self.alice, role="Python", rating="3.00"
        )
        older_python.created_at = timezone.now() - timedelta(days=40)
        older_python.save(update_fields=["created_at"])
        MockInterviewSession.objects.create(
            student=self.alice, role="Django", rating="5.00"
        )
        MockInterviewSession.objects.create(
            student=self.bob, role="Python", rating="2.00"
        )
        other_admin = User.objects.create_user(
            "other-admin", password="pw12345!", is_admin=True
        )
        other_group = Group.objects.create(name="Other batch", admin=other_admin)
        other_student = User.objects.create_user(
            "other-student", password="pw12345!", is_student=True
        )
        GroupMembership.objects.create(group=other_group, student=other_student)
        MockInterviewSession.objects.create(
            student=other_student, role="Private role", rating="2.00"
        )
        today = timezone.localdate()
        request = RequestFactory().get(reverse("admin_mock_interviews"), {
            "batch": self.group.pk,
            "student": self.alice.pk,
            "topic": "Python",
            "from_date": (today - timedelta(days=2)).isoformat(),
            "to_date": today.isoformat(),
        })
        request.user = self.admin

        with patch("tracker.views.render") as mock_render:
            from .views import admin_mock_interviews

            admin_mock_interviews(request)

        sessions = list(mock_render.call_args.args[2]["page_obj"].object_list)
        self.assertEqual(sessions, [recent_python])

    def test_admin_mock_reviews_scope_unfiltered_sessions(self):
        owned_session = MockInterviewSession.objects.create(
            student=self.alice, role="Python"
        )
        hr = User.objects.create_user(
            "managed-hr", password="pw12345!", is_hr=True, created_by=self.admin
        )
        hr_student = User.objects.create_user(
            "hr-created-student", password="pw12345!", is_student=True, created_by=hr
        )
        hr_session = MockInterviewSession.objects.create(
            student=hr_student, role="Django"
        )
        other_admin = User.objects.create_user(
            "reviews-admin", password="pw12345!", is_admin=True
        )
        other_group = Group.objects.create(name="Reviews private batch", admin=other_admin)
        other_student = User.objects.create_user(
            "reviews-private", password="pw12345!", is_student=True
        )
        GroupMembership.objects.create(group=other_group, student=other_student)
        MockInterviewSession.objects.create(student=other_student, role="Private topic")
        request = RequestFactory().get(reverse("admin_mock_interviews"), {
            "batch": "invalid",
            "student": "invalid",
        })
        request.user = self.admin

        with patch("tracker.views.render") as mock_render:
            from .views import admin_mock_interviews

            admin_mock_interviews(request)

        sessions = list(mock_render.call_args.args[2]["page_obj"].object_list)
        self.assertEqual({session.pk for session in sessions}, {owned_session.pk, hr_session.pk})

    def test_admin_can_create_hr_account(self):
        self.client.force_login(self.admin)

        response = self.client.post(reverse("admin_hr_user_add"), {
            "username": "new-hr",
            "email": "new-hr@example.com",
            "password1": "S7rong-HR-password-99",
            "password2": "S7rong-HR-password-99",
        })

        self.assertRedirects(
            response, reverse("admin_hr_user_add"), fetch_redirect_response=False
        )
        hr = User.objects.get(username="new-hr")
        self.assertTrue(hr.is_hr)
        self.assertFalse(hr.is_admin)
        self.assertFalse(hr.is_student)
        self.assertEqual(hr.created_by, self.admin)

    def test_hr_can_add_and_only_list_their_students(self):
        hr = User.objects.create_user(
            "hr-owner", password="pw12345!", is_hr=True, created_by=self.admin
        )
        other_hr = User.objects.create_user(
            "hr-other", password="pw12345!", is_hr=True, created_by=self.admin
        )
        User.objects.create_user(
            "other-hr-student", password="pw12345!", is_student=True, created_by=other_hr
        )
        self.client.force_login(hr)

        response = self.client.post(reverse("admin_student_add"), {
            "username": "hr-student",
            "email": "hr-student@example.com",
            "password1": "S7rong-student-password-99",
            "password2": "S7rong-student-password-99",
        })

        self.assertRedirects(
            response, reverse("hr_students"), fetch_redirect_response=False
        )
        student = User.objects.get(username="hr-student")
        self.assertTrue(student.is_student)
        self.assertEqual(student.created_by, hr)

        other_admin = User.objects.create_user("batch-owner", is_admin=True)
        other_group = Group.objects.create(name="Private batch", admin=other_admin)
        GroupMembership.objects.create(group=other_group, student=self.bob)
        request = RequestFactory().get(reverse("hr_students"))
        request.user = hr
        with patch("tracker.views.render") as mock_render:
            from .views import hr_students

            hr_students(request)
        visible_students = list(mock_render.call_args.args[2]["students"])
        self.assertEqual(set(visible_students), {self.alice, self.bob, student})

        batch_request = RequestFactory().get(reverse("admin_groups"))
        batch_request.user = hr
        with patch("tracker.views.render") as mock_render:
            from .views import admin_groups

            admin_groups(batch_request)
        visible_batches = list(mock_render.call_args.args[2]["groups"])
        self.assertEqual(visible_batches, [self.group])
        self.assertFalse(mock_render.call_args.args[2]["can_manage"])
        self.assertEqual(
            self.client.get(reverse("admin_group_detail", args=[other_group.pk])).status_code,
            404,
        )

        admin_request = RequestFactory().get(reverse("admin_students"))
        admin_request.user = self.admin
        with patch("tracker.views.render") as mock_render:
            from .views import admin_students

            admin_students(admin_request)
        admin_visible_students = list(mock_render.call_args.args[2]["students"])
        self.assertIn(student, admin_visible_students)

    def test_admin_mock_reviews_paginate_sessions(self):
        for _ in range(27):
            MockInterviewSession.objects.create(student=self.alice, role="Python")
        request = RequestFactory().get(reverse("admin_mock_interviews"), {
            "batch": self.group.pk,
            "student": self.alice.pk,
            "topic": "Python",
            "page": 2,
        })
        request.user = self.admin

        with patch("tracker.views.render") as mock_render:
            from .views import admin_mock_interviews

            admin_mock_interviews(request)

        context = mock_render.call_args.args[2]
        self.assertEqual(context["result_count"], 27)
        self.assertEqual(len(context["page_obj"].object_list), 2)
        self.assertTrue(context["previous_page_url"])
        self.assertIsNone(context["next_page_url"])

    def test_main_pages_render_for_each_role(self):
        self.client.force_login(self.admin)
        for name, args in (
            ("admin_dashboard", ()),
            ("admin_interviews", ()),
            ("admin_groups", ()),
            ("admin_group_add", ()),
            ("admin_students", ()),
            ("admin_student_add", ()),
            ("admin_registrations", ()),
            ("admin_courses", ()),
            ("admin_reports", ()),
            ("admin_group_detail", (self.group.pk,)),
            ("admin_student_detail", (self.alice.pk,)),
        ):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "Tweak Talent")
            self.assertContains(response, "/static/css/app.css")
            self.assertNotContains(response, "cdn.tailwindcss.com")

        self.client.force_login(self.alice)
        for name, args in (
            ("student_dashboard", ()),
            ("student_courses", ()),
            ("student_interview_questions", ()),
            ("student_interviews", ()),
            ("student_interview_add", ()),
            ("student_interview_detail", (self.iv.pk,)),
        ):
            response = self.client.get(reverse(name, args=args))
            self.assertEqual(response.status_code, 200, name)
            self.assertContains(response, "Tweak Talent")

    def test_student_directory_is_scoped_to_admin_groups(self):
        unassigned = User.objects.create_user("unassigned", is_student=True)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("admin_students"))

        self.assertContains(response, "alice")
        self.assertContains(response, "bob")
        self.assertNotContains(response, "unassigned")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin_student_detail", args=[unassigned.pk])).status_code,
            404,
        )

    def test_students_created_by_admin_are_in_directory(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin_student_add"), {
            "username": "created-student",
            "email": "created@example.com",
            "password1": "T7rong-pass-99",
            "password2": "T7rong-pass-99",
        })

        student = User.objects.get(username="created-student")
        self.assertEqual(student.created_by, self.admin)
        self.assertRedirects(
            response, reverse("admin_student_detail", args=[student.pk]))
        self.assertEqual(self.client.get(reverse("admin_students")).status_code, 200)
        self.assertEqual(
            self.client.get(reverse("admin_student_detail", args=[student.pk])).status_code,
            200,
        )

    def test_student_directory_supports_search_and_three_views(self):
        self.client.force_login(self.admin)
        for mode in ("cards", "table", "list"):
            response = self.client.get(reverse("admin_students"), {"view": mode})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["view_mode"], mode)
            self.assertContains(response, "alice")
            self.assertContains(response, "Batch A")

        response = self.client.get(
            reverse("admin_students"), {"view": "table", "q": "alice"}
        )
        self.assertContains(response, "alice")
        self.assertNotContains(response, "bob")

    def test_registration_queue_renders_verified_applications_and_decision_actions(self):
        registration = StudentRegistrationRequest.objects.create(
            username="pending-student",
            email="pending@example.com",
            password_hash="hashed-password",
            verification_code_hash="a" * 64,
            verification_expires_at=timezone.now() + timedelta(minutes=10),
            status=StudentRegistrationRequest.AWAITING_APPROVAL,
            verified_at=timezone.now(),
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("admin_registrations"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["pending_count"], 1)
        self.assertContains(response, "Registration review")
        self.assertContains(response, registration.email)
        self.assertContains(response, "Approve")
        self.assertContains(response, "Decline")
        self.assertContains(response, "data-confirm=")

    def test_other_admin_cannot_view_student_details(self):
        other = User.objects.create_user("admin2", is_admin=True)
        self.client.force_login(other)
        self.assertEqual(
            self.client.get(reverse("admin_student_detail", args=[self.alice.pk])).status_code,
            404,
        )

    def test_admin_creates_group_and_adds_member(self):
        self.client.force_login(self.admin)
        unassigned = User.objects.create_user("unassigned-member", is_student=True)
        r = self.client.post(reverse("admin_group_add"), {"name": "Batch B", "description": ""})
        g = Group.objects.get(name="Batch B")
        self.assertRedirects(r, reverse("admin_group_detail", args=[g.pk]))
        response = self.client.get(reverse("admin_group_member_add", args=[g.pk]))
        self.assertContains(response, "unassigned-member")
        self.assertNotContains(response, "alice")
        self.client.post(
            reverse("admin_group_member_add", args=[g.pk]),
            {"student": unassigned.pk},
        )
        self.assertTrue(g.memberships.filter(student=unassigned).exists())

    def test_student_adds_interview_only_for_own_group(self):
        outsider_group = Group.objects.create(name="Other", admin=self.admin)
        self.client.force_login(self.alice)
        data = {"company_name": "Globex", "role": "QA", "date_of_interview": "2026-01-10",
                "time_of_interview": "10:30", "interview_type": "walk_in", "hr_name": "Pat", "hr_contact_number": "555"}
        r = self.client.post(
            reverse("student_interview_add"),
            {**data, "group": outsider_group.pk},
        )
        self.assertEqual(r.status_code, 200)  # form error, nothing created
        self.assertFalse(Interview.objects.filter(company_name="Globex").exists())
        self.client.post(
            reverse("student_interview_add"),
            {**data, "group": self.group.pk},
        )
        self.assertTrue(Interview.objects.filter(company_name="Globex", student=self.alice).exists())

    def test_student_saves_hr_contact_details(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("student_interview_add"), {
            "group": self.group.pk,
            "company_name": "Globex",
            "role": "QA",
            "job_posting_url": "https://example.com/jobs/qa",
            "date_of_interview": "2026-01-10",
            "time_of_interview": "14:00",
            "interview_type": "referral",
            "hr_name": "Jordan Lee",
            "hr_contact_number": "+1 555 0123",
            "hr_email": "jordan@example.com",
        })

        interview = Interview.objects.get(company_name="Globex")
        self.assertRedirects(
            response, reverse("student_interview_detail", args=[interview.pk]))
        self.assertEqual(interview.hr_name, "Jordan Lee")
        self.assertEqual(interview.hr_contact_number, "+1 555 0123")
        self.assertEqual(interview.hr_email, "jordan@example.com")
        self.assertEqual(interview.job_posting_url, "https://example.com/jobs/qa")
        self.assertContains(
            self.client.get(reverse("student_interview_detail", args=[interview.pk])),
            "jordan@example.com",
        )

    def test_interview_table_links_company_and_role_posting_for_both_dashboards(self):
        self.iv.job_posting_url = "https://example.com/jobs/dev"
        self.iv.save(update_fields=["job_posting_url"])

        self.client.force_login(self.alice)
        student_response = self.client.get(
            reverse("student_interviews"), {"view": "table"})
        self.assertContains(
            student_response,
            reverse("student_interview_detail", args=[self.iv.pk]),
        )
        self.assertContains(
            student_response,
            reverse("student_interview_edit", args=[self.iv.pk]),
        )
        self.assertContains(student_response, 'href="https://example.com/jobs/dev"')
        student_dashboard_response = self.client.get(reverse("student_dashboard"))
        self.assertContains(
            student_dashboard_response, 'href="https://example.com/jobs/dev"')

        self.client.force_login(self.admin)
        admin_response = self.client.get(
            reverse("admin_interviews"), {"view": "table"})
        self.assertContains(
            admin_response,
            reverse("admin_interview_detail", args=[self.iv.pk]),
        )
        self.assertContains(admin_response, 'href="https://example.com/jobs/dev"')
        detail_response = self.client.get(
            reverse("admin_interview_detail", args=[self.iv.pk]))
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, "Interview rounds")

    def test_missing_job_link_is_visible_and_actionable(self):
        self.client.force_login(self.alice)
        student_response = self.client.get(reverse("student_dashboard"))
        self.assertContains(student_response, "Add job link")
        self.assertContains(
            student_response, reverse("student_interview_edit", args=[self.iv.pk]))

        self.client.force_login(self.admin)
        admin_response = self.client.get(reverse("admin_dashboard"))
        self.assertContains(admin_response, "Not provided")
        self.assertContains(
            admin_response, reverse("admin_interview_detail", args=[self.iv.pk]))

    def test_student_course_and_question_pages_show_seeded_content(self):
        self.client.force_login(self.alice)

        course_response = self.client.get(reverse("student_courses"))
        self.assertEqual(course_response.status_code, 200)
        self.assertContains(course_response, "Python")
        self.assertContains(course_response, "Suggested roadmap")
        self.assertContains(course_response, "Set up Python and learn basic syntax")
        self.assertContains(course_response, "SQL (PostgreSQL)")
        self.assertEqual(LearningCourse.objects.count(), 7)

        questions_response = self.client.get(reverse("student_interview_questions"))
        self.assertEqual(questions_response.status_code, 200)
        self.assertContains(questions_response, "PRACTICE FOR YOUR NEXT STEP")
        self.assertContains(questions_response, "What is the difference between a list and a tuple?")
        self.assertContains(questions_response, "How do migrations keep a database schema in sync with models?")

    def test_admin_can_edit_and_preview_learning_content(self):
        course = LearningCourse.objects.get(name="Python")
        self.client.force_login(self.admin)
        manager = self.client.get(reverse("admin_courses"))
        self.assertContains(manager, reverse("admin_course_edit", args=[course.pk]))
        self.assertContains(manager, reverse("admin_course_preview", args=[course.pk]))

        response = self.client.post(
            reverse("admin_course_edit", args=[course.pk]),
            {
                "name": course.name,
                "summary": "Updated Python summary",
                "level": course.level,
                "duration": course.duration,
                "prerequisites": course.prerequisites,
                "learning_outcomes": course.learning_outcomes,
                "tools": course.tools,
                "roadmap": "New roadmap step",
                "interview_questions": "New sample question",
                "sort_order": course.sort_order,
            },
        )

        self.assertRedirects(response, reverse("admin_course_edit", args=[course.pk]))
        course.refresh_from_db()
        self.assertEqual(course.summary, "Updated Python summary")
        preview = self.client.get(reverse("admin_course_preview", args=[course.pk]))
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "New roadmap step")
        self.assertContains(preview, "New sample question")

    def test_admin_reports_show_outcomes_and_are_group_scoped(self):
        self.iv.job_posting_url = "https://example.com/acme"
        self.iv.hr_name = "Jordan Recruiter"
        self.iv.hr_email = "jordan@example.com"
        self.iv.save(update_fields=["job_posting_url", "hr_name", "hr_email"])
        InterviewStatus.objects.create(
            interview=self.iv, final_status=InterviewStatus.SELECTED)
        not_selected = Interview.objects.create(
            student=self.bob,
            group=self.group,
            company_name="Globex",
            role="Analyst",
            job_posting_url="https://example.com/globex",
            date_of_interview=date.today(),
            hr_name="Taylor HR",
            hr_contact_number="555-0100",
            hr_email="taylor@example.com",
        )
        InterviewStatus.objects.create(
            interview=not_selected,
            final_status=InterviewStatus.NOT_SELECTED,
        )
        other_admin = User.objects.create_user("report-admin", is_admin=True)
        other_group = Group.objects.create(name="Other group", admin=other_admin)
        GroupMembership.objects.create(group=other_group, student=self.bob)
        outside_interview = Interview.objects.create(
            student=self.bob,
            group=other_group,
            company_name="Private Co",
            role="Engineer",
            date_of_interview=date.today(),
        )
        InterviewStatus.objects.create(
            interview=outside_interview,
            final_status=InterviewStatus.SELECTED,
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_reports"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Selected")
        self.assertContains(response, "Not selected")
        self.assertContains(response, "Jordan Recruiter")
        self.assertContains(response, "https://example.com/acme")
        self.assertContains(response, "https://example.com/globex")
        self.assertNotContains(response, "Private Co")

    def test_only_admin_can_manage_courses_and_view_reports(self):
        course = LearningCourse.objects.get(name="Python")
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("admin_courses")).status_code, 403)
        self.assertEqual(
            self.client.get(reverse("admin_course_preview", args=[course.pk])).status_code,
            403,
        )
        self.assertEqual(self.client.get(reverse("admin_reports")).status_code, 403)

    def test_final_result_updates_rounds_and_redirects_to_interview_list(self):
        first = InterviewRound.objects.create(
            interview=self.iv, round_number=1, description="Screening",
            status=InterviewRound.REJECTED,
        )
        last = InterviewRound.objects.create(
            interview=self.iv, round_number=2, description="Final",
            status=InterviewRound.CLEARED,
        )
        self.client.force_login(self.alice)
        url = reverse("interview_status", args=[self.iv.pk])

        selected_response = self.client.post(url, {"final_status": "selected"})

        self.assertRedirects(selected_response, reverse("student_interviews"))
        first.refresh_from_db()
        last.refresh_from_db()
        self.assertEqual(first.status, InterviewRound.CLEARED)
        self.assertEqual(last.status, InterviewRound.CLEARED)

        rejected_response = self.client.post(url, {
            "final_status": "not-selected",
            "edit_mode": "1",
        })

        self.assertRedirects(rejected_response, reverse("student_interviews"))
        first.refresh_from_db()
        last.refresh_from_db()
        self.assertEqual(first.status, InterviewRound.CLEARED)
        self.assertEqual(last.status, InterviewRound.REJECTED)

    def test_finalized_interview_requires_edit_mode_for_round_changes(self):
        rnd = InterviewRound.objects.create(
            interview=self.iv, round_number=1, description="Technical",
            status=InterviewRound.CLEARED,
        )
        InterviewStatus.objects.create(
            interview=self.iv, final_status=InterviewStatus.SELECTED)
        self.client.force_login(self.alice)

        detail_response = self.client.get(
            reverse("student_interview_detail", args=[self.iv.pk]))
        self.assertNotContains(detail_response, "Add round")
        self.assertNotContains(detail_response, "Save result")
        self.assertNotContains(detail_response, 'class="form-control round-status"')
        self.assertContains(
            detail_response,
            reverse("student_interview_edit", args=[self.iv.pk]),
        )
        edit_response = self.client.get(
            reverse("student_interview_edit", args=[self.iv.pk]))
        self.assertNotContains(edit_response, "Add round")
        self.assertNotContains(edit_response, "Save result")
        progress_response = self.client.get(
            reverse("student_interview_progress", args=[self.iv.pk]))
        self.assertContains(progress_response, "Add round")
        self.assertContains(progress_response, "Save result")

        update_response = self.client.post(
            reverse("round_update", args=[rnd.pk]),
            {"status": "pending", "edit_mode": "1"},
        )
        self.assertEqual(update_response.status_code, 200)
        self.assertFalse(InterviewStatus.objects.filter(interview=self.iv).exists())

    def test_admin_interview_detail_is_scoped_to_admin_groups(self):
        other_admin = User.objects.create_user("another-admin", is_admin=True)
        self.client.force_login(other_admin)
        response = self.client.get(
            reverse("admin_interview_detail", args=[self.iv.pk]))
        self.assertEqual(response.status_code, 404)

    def test_removing_student_from_group_preserves_interview_history(self):
        InterviewRound.objects.create(
            interview=self.iv, round_number=1, description="Technical")
        InterviewStatus.objects.create(interview=self.iv, final_status="selected")
        another_group = Group.objects.create(name="Batch B", admin=self.admin)
        GroupMembership.objects.create(group=another_group, student=self.alice)
        other_interview = Interview.objects.create(
            student=self.alice,
            group=another_group,
            company_name="Other company",
            role="Engineer",
            date_of_interview=date.today(),
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            reverse("member_remove", args=[self.group.pk, self.alice.pk]))

        self.assertRedirects(response, reverse("admin_group_detail", args=[self.group.pk]))
        self.assertFalse(GroupMembership.objects.filter(
            group=self.group, student=self.alice).exists())
        self.assertTrue(Interview.objects.filter(pk=self.iv.pk).exists())
        self.assertTrue(InterviewRound.objects.filter(interview_id=self.iv.pk).exists())
        self.assertTrue(InterviewStatus.objects.filter(interview_id=self.iv.pk).exists())
        self.assertTrue(Interview.objects.filter(pk=other_interview.pk).exists())
        self.assertTrue(GroupMembership.objects.filter(
            group=another_group, student=self.alice).exists())

        new_group = Group.objects.create(name="Batch C", admin=self.admin)
        add_response = self.client.post(
            reverse("admin_group_member_add", args=[new_group.pk]),
            {"student": self.alice.pk},
        )
        self.assertEqual(add_response.status_code, 200)
        self.assertFalse(GroupMembership.objects.filter(
            group=new_group, student=self.alice).exists())
        available_response = self.client.get(
            reverse("admin_group_member_add", args=[new_group.pk]))
        self.assertNotContains(available_response, "alice")
        self.client.force_login(self.admin)
        group_response = self.client.get(reverse("admin_group_detail", args=[new_group.pk]))
        self.assertNotContains(group_response, "Acme")
        same_group_response = self.client.post(
            reverse("admin_group_member_add", args=[self.group.pk]),
            {"student": self.alice.pk},
        )
        self.assertEqual(same_group_response.status_code, 200)

        self.client.force_login(self.alice)
        response = self.client.get(reverse("student_interviews"))
        self.assertContains(response, "Acme")
        self.assertContains(response, "Other company")

    def test_deleting_membership_directly_preserves_interviews(self):
        membership = GroupMembership.objects.get(group=self.group, student=self.alice)

        membership.delete()

        self.assertTrue(Interview.objects.filter(pk=self.iv.pk).exists())

    def test_ajax_add_rounds_and_validation(self):
        self.client.force_login(self.alice)
        url = reverse("round_add", args=[self.iv.pk])
        response = self.client.post(url, {"round_type": "coding", "description": "Coding test"})
        self.assertEqual(response.status_code, 201)
        self.assertIn('class="form-control round-status"', response.json()["html"])
        self.assertEqual(self.client.post(url, {"round_type": "hr"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"round_type": "hr", "scheduled_date": "2030-01-02"}).status_code, 400)
        ok = self.client.post(url, {"round_type": "hr", "scheduled_date": "2030-01-02", "scheduled_time": "11:30"})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(self.iv.rounds.get(round_number=2).description, "HR round")
        self.assertEqual(list(self.iv.rounds.values_list("round_number", flat=True)), [1, 2])
        self.assertEqual(self.client.post(url, {"description": "  "}).status_code, 400)
        self.assertEqual(self.client.post(url, {"round_type": "other", "scheduled_date": "2030-01-03",
                                                "scheduled_time": "10:00"}).status_code, 400)

    def test_csrf_enforced_on_ajax(self):
        from django.test import Client
        c = Client(enforce_csrf_checks=True)
        c.force_login(self.alice)
        self.assertEqual(c.post(reverse("round_add", args=[self.iv.pk]), {"description": "x"}).status_code, 403)

    def test_final_status_requires_completed_rounds(self):
        self.client.force_login(self.alice)
        url = reverse("interview_status", args=[self.iv.pk])
        self.client.post(url, {"final_status": "selected"})
        self.assertFalse(InterviewStatus.objects.filter(interview=self.iv).exists())  # no rounds
        rnd = InterviewRound.objects.create(interview=self.iv, round_number=1, description="x")
        self.client.post(url, {"final_status": "selected"})
        self.assertFalse(InterviewStatus.objects.filter(interview=self.iv).exists())  # pending round
        self.client.post(reverse("round_update", args=[rnd.pk]), {"status": "cleared"})
        self.client.post(url, {"final_status": "selected"})
        self.assertEqual(InterviewStatus.objects.get(interview=self.iv).final_status, "selected")

    def test_pending_round_reopens_finalized_interview(self):
        self.client.force_login(self.alice)
        rnd = InterviewRound.objects.create(
            interview=self.iv, round_number=1, description="Technical", status="cleared")
        InterviewStatus.objects.create(interview=self.iv, final_status="selected")

        response = self.client.post(
            reverse("round_update", args=[rnd.pk]), {"status": "pending"})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {
            "status": "pending",
            "badge": "status-pending",
            "final_status": "in-progress",
            "final_label": "In progress",
        })
        self.assertFalse(InterviewStatus.objects.filter(interview=self.iv).exists())


class MockProgressTests(TestCase):
    def test_progress_summarises_topics_levels_and_suggestion(self):
        from tracker.models import MockInterviewSession
        from tracker.views import _mock_progress

        student = User.objects.create_user("prog", password="pw12345!", is_student=True)
        for topic, rating, level in [("Python", 2, "easy"), ("SQL", 4, "medium")]:
            MockInterviewSession.objects.create(
                student=student, role=topic, rating=rating, difficulty=level)
        progress = _mock_progress(student)
        self.assertEqual(progress["topics"][0]["topic"], "Python")
        self.assertTrue(progress["topics"][0]["weak"])
        self.assertEqual(progress["suggestion"]["topic"], "Python")
        self.assertEqual([x["level"] for x in progress["levels"]], ["Easy", "Medium"])

    def test_integrity_score_and_level(self):
        from tracker.models import MockInterviewSession

        session = MockInterviewSession(integrity_events=[{"type": "paste"}] * 3)
        self.assertEqual(session.integrity_score, 70)
        self.assertEqual(session.integrity_level, "medium")


class MockLiteModeTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("lite", password="pw12345!", is_student=True)
        self.client.force_login(self.student)
        from tracker.models import MockInterviewSession
        self.session = MockInterviewSession.objects.create(student=self.student, role="Python")
        self.url = reverse("student_mock_interview_ai")

    def post(self, **extra):
        payload = {"action": "feedback", "consent": True, "role": "Python", "session_id": self.session.pk,
                   "question_number": 1, "question": "What is a list?", **extra}
        return self.client.post(self.url, payload, content_type="application/json")

    @patch("tracker.views.generate_json", return_value={"score": 4, "answer_feedback": "Good."})
    def test_text_answer_without_camera_is_scored(self, mock_ai):
        response = self.post(text="A list is an ordered, mutable sequence.", lite=True)
        self.assertEqual(response.status_code, 200)
        score = self.session.scores.get(question_number=1)
        self.assertEqual(score.score, 4)
        self.assertEqual(score.camera_feedback, "")
        self.assertEqual(len(mock_ai.call_args.args[0]), 1)

    def test_text_answer_requires_lite_flag(self):
        self.assertEqual(self.post(text="answer").status_code, 400)

    def test_empty_text_is_rejected(self):
        self.assertEqual(self.post(text="  ", lite=True).status_code, 400)


class PrepTrackerTests(TestCase):
    def setUp(self):
        from tracker.models import Group, GroupMembership, Interview, InterviewRound
        self.student = User.objects.create_user("prep", password="pw12345!", is_student=True)
        admin = User.objects.create_user("prepadmin", password="pw12345!", is_admin=True)
        group = Group.objects.create(name="B1", admin=admin)
        GroupMembership.objects.create(group=group, student=self.student)
        today = timezone.localdate()
        self.iv = Interview.objects.create(
            student=self.student, group=group, company_name="Acme", role="Dev",
            date_of_interview=today + timedelta(days=1))
        self.rnd = InterviewRound.objects.create(
            interview=self.iv, round_number=1, description="Technical",
            scheduled_date=today + timedelta(days=2))
        self.client.force_login(self.student)

    def test_notes_are_saved(self):
        response = self.client.post(reverse("interview_notes", args=[self.iv.pk]), {"prep_notes": "Revise SQL"})
        self.assertEqual(response.status_code, 302)
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.prep_notes, "Revise SQL")

    def test_round_date_can_be_changed_but_not_by_others(self):
        url = reverse("round_schedule", args=[self.rnd.pk])
        self.assertEqual(self.client.post(url, {"scheduled_date": "2030-01-05"}).status_code, 200)
        self.rnd.refresh_from_db()
        self.assertEqual(str(self.rnd.scheduled_date), "2030-01-05")
        other = User.objects.create_user("other", password="pw12345!", is_student=True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"scheduled_date": "2030-02-01"}).status_code, 404)

    def test_reminders_and_calendar(self):
        from tracker.views import _reminders
        items = _reminders(self.student)
        self.assertEqual([i["when"] for i in items], ["Tomorrow", "In 2 days"])
        response = self.client.get(reverse("student_calendar"))
        self.assertContains(response, "Acme interview")
        self.assertContains(response, "Acme: Technical")
        self.assertEqual(self.client.get(reverse("student_calendar") + "?month=bad").status_code, 200)
        self.assertContains(self.client.get(reverse("student_dashboard")), "Coming up this week")


class RetentionTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        from django.utils import timezone
        self.student = User.objects.create_user("ret", password="pw", is_student=True)
        events = [{"type": "tab_hidden", "at": "x"}, {"type": "copy", "at": "x"}]
        self.old = MockInterviewSession.objects.create(
            student=self.student, role="Python", rating=4, integrity_events=events)
        MockInterviewSession.objects.filter(pk=self.old.pk).update(
            created_at=timezone.now() - timedelta(days=31))
        self.new = MockInterviewSession.objects.create(
            student=self.student, role="Python", rating=3, integrity_events=events)

    def test_purge_keeps_score_and_rating(self):
        from .retention import purge_old_mock_data
        before = MockInterviewSession.objects.get(pk=self.old.pk).integrity_score
        self.assertEqual(purge_old_mock_data(30), 1)
        old = MockInterviewSession.objects.get(pk=self.old.pk)
        self.assertEqual(old.integrity_events, [])
        self.assertEqual(old.integrity_score, before)
        self.assertEqual(old.integrity_flag_count, 2)
        self.assertEqual(float(old.rating), 4.0)
        self.assertEqual(len(MockInterviewSession.objects.get(pk=self.new.pk).integrity_events), 2)

    def test_cron_requires_secret(self):
        with self.settings(CRON_SECRET="s3"):
            self.assertEqual(self.client.get("/cron/purge-mock-data/").status_code, 403)
            ok = self.client.get("/cron/purge-mock-data/", HTTP_AUTHORIZATION="Bearer s3")
            self.assertEqual(ok.json()["purged"], 1)


class CsrfLogoutTests(TestCase):
    def test_stale_token_logout_still_logs_out(self):
        User.objects.create_user("csrfu", password="pw", is_student=True)
        client = Client(enforce_csrf_checks=True)
        client.login(username="csrfu", password="pw")
        response = client.post("/logout/")
        self.assertRedirects(response, "/login/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", client.session)


class AdminStudentProgressTests(TestCase):
    def test_admin_sees_mock_progress(self):
        admin = User.objects.create_user("adm9", password="pw", is_admin=True)
        student = User.objects.create_user("stu9", password="pw", is_student=True, created_by=admin)
        MockInterviewSession.objects.create(student=student, role="Python", rating=4, difficulty="easy")
        self.client.login(username="adm9", password="pw")
        response = self.client.get(f"/admin/students/{student.pk}/")
        self.assertContains(response, "Mock interview progress")
        self.assertContains(response, "By topic")
        self.assertContains(response, "Python")


class AdminDashboardBatchFilterTests(TestCase):
    def test_filter_by_batch(self):
        admin = User.objects.create_user("adm8", password="pw", is_admin=True)
        a = User.objects.create_user("inbatch", password="pw", is_student=True, created_by=admin)
        b = User.objects.create_user("otherbatch", password="pw", is_student=True, created_by=admin)
        g1 = Group.objects.create(name="B1", admin=admin)
        g2 = Group.objects.create(name="B2", admin=admin)
        GroupMembership.objects.create(group=g1, student=a)
        GroupMembership.objects.create(group=g2, student=b)
        MockInterviewSession.objects.create(student=a, role="Python", rating=4)
        self.client.login(username="adm8", password="pw")
        response = self.client.get(f"/admin/dashboard/?batch={g1.pk}")
        self.assertContains(response, "Students progress")
        self.assertContains(response, "inbatch")
        self.assertNotContains(response, "otherbatch")
        self.assertContains(response, "Mock 4.0/5")
        self.assertContains(self.client.get("/admin/dashboard/"), "otherbatch")


class InterviewTimeAndAdminCalendarTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user("adm7", password="pw", is_admin=True)
        self.stu = User.objects.create_user("stu7", password="pw", is_student=True, created_by=self.admin)
        self.group = Group.objects.create(name="B7", admin=self.admin)
        GroupMembership.objects.create(group=self.group, student=self.stu)

    def test_time_and_hr_details_required(self):
        self.client.force_login(self.stu)
        response = self.client.post(reverse("student_interview_add"), {
            "group": self.group.pk, "company_name": "Acme", "role": "Dev",
            "date_of_interview": "2026-01-10"})
        self.assertEqual(response.status_code, 200)
        for field in ("time_of_interview", "hr_name", "hr_contact_number", "interview_type"):
            self.assertIn(field, response.context["form"].errors)
        self.assertFalse(Interview.objects.exists())

    def test_admin_calendar_shows_student_company_and_time(self):
        import datetime
        today = timezone.localdate()
        Interview.objects.create(
            student=self.stu, group=self.group, company_name="Initech", role="Dev",
            date_of_interview=today, time_of_interview=datetime.time(15, 30))
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_calendar"))
        self.assertContains(response, "stu7 · Initech")
        self.assertContains(response, "03:30 PM")
        other = Group.objects.create(name="B8", admin=self.admin)
        self.assertNotContains(
            self.client.get(reverse("admin_calendar") + f"?batch={other.pk}"), "Initech")


class InterviewManagementTests(TestCase):
    def setUp(self):
        import datetime
        self.admin = User.objects.create_user("adm6", password="pw", is_admin=True)
        self.stu = User.objects.create_user("stu6", password="pw", is_student=True, created_by=self.admin)
        self.group = Group.objects.create(name="B6", admin=self.admin)
        GroupMembership.objects.create(group=self.group, student=self.stu)
        self.today = timezone.localdate()
        self.iv = Interview.objects.create(
            student=self.stu, group=self.group, company_name="Acme", role="Dev",
            date_of_interview=self.today, time_of_interview=datetime.time(10, 0),
            interview_type="walk_in", hr_name="H", hr_contact_number="1")
        self.form_data = {
            "group": self.group.pk, "company_name": "Acme", "role": "Dev",
            "date_of_interview": self.today.isoformat(), "time_of_interview": "11:00",
            "interview_type": "referral", "hr_name": "H", "hr_contact_number": "1",
        }

    def test_duplicate_warns_then_allows_with_confirmation(self):
        self.client.force_login(self.stu)
        url = reverse("student_interview_add")
        response = self.client.post(url, self.form_data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "already have an interview for Dev at Acme")
        self.assertContains(response, "Save anyway")
        self.assertEqual(Interview.objects.count(), 1)
        self.client.post(url, {**self.form_data, "confirm_clash": "on"})
        self.assertEqual(Interview.objects.count(), 2)

    def test_same_slot_clash_warns(self):
        self.client.force_login(self.stu)
        data = {**self.form_data, "company_name": "Other", "time_of_interview": "10:00"}
        response = self.client.post(reverse("student_interview_add"), data)
        self.assertContains(response, "same date and time")

    def test_admin_filters_and_csv_export(self):
        Interview.objects.create(
            student=self.stu, group=self.group, company_name="Globex", role="QA",
            date_of_interview=self.today, interview_type="hr_call", hr_name="Zed", hr_contact_number="999")
        self.client.force_login(self.admin)
        page = self.client.get(reverse("admin_interviews") + "?type=hr_call")
        self.assertContains(page, "Globex")
        self.assertNotContains(page, ">Acme<")
        page = self.client.get(reverse("admin_interviews") + "?company=acm&view=table")
        self.assertContains(page, "Acme")
        self.assertNotContains(page, "Globex")
        csv_response = self.client.get(reverse("admin_interviews_export") + "?type=hr_call")
        body = csv_response.content.decode()
        self.assertIn("Globex", body)
        self.assertIn("Zed", body)
        self.assertNotIn("Acme", body)

    def test_trainer_note_visibility(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_interview_notes", args=[self.iv.pk]),
                         {"admin_notes": "Practice system design"})
        self.client.force_login(self.stu)
        detail = reverse("student_interview_detail", args=[self.iv.pk])
        self.assertNotContains(self.client.get(detail), "Practice system design")
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_interview_notes", args=[self.iv.pk]),
                         {"admin_notes": "Practice system design", "notes_visible_to_student": "on"})
        self.client.force_login(self.stu)
        self.assertContains(self.client.get(detail), "Practice system design")

    def test_quick_actions(self):
        self.client.force_login(self.stu)
        url = reverse("student_interview_quick", args=[self.iv.pk])
        self.client.post(url, {"action": "no_show"})
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.attendance, "no_show")
        self.client.post(url, {"action": "no_show"})
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.attendance, "")
        self.client.post(url, {"action": "offer"})
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.final_status, "selected")
        other = User.objects.create_user("stuX", password="pw", is_student=True)
        self.client.force_login(other)
        self.assertEqual(self.client.post(url, {"action": "attended"}).status_code, 404)
        self.client.force_login(self.admin)
        self.client.post(reverse("admin_interview_quick", args=[self.iv.pk]), {"action": "rescheduled"})
        self.iv.refresh_from_db()
        self.assertEqual(self.iv.attendance, "rescheduled")

    def test_calendar_views_and_more_popup(self):
        for i in range(4):
            Interview.objects.create(
                student=self.stu, group=self.group, company_name=f"Co{i}", role="R",
                date_of_interview=self.today, interview_type="walk_in", hr_name="h", hr_contact_number="1")
        self.client.force_login(self.admin)
        base = reverse("admin_calendar")
        month = self.client.get(base)
        self.assertContains(month, "more</button>")
        self.assertContains(month, "calendar-mobile-agenda")
        for view in ("week", "day", "list"):
            page = self.client.get(f"{base}?view={view}&date={self.today.isoformat()}")
            self.assertContains(page, "Co3")
        self.assertContains(self.client.get(base + "?view=week&date=bogus"), "calendar-week")
        self.client.force_login(self.stu)
        self.assertContains(self.client.get(reverse("student_calendar") + "?view=list"), "Acme")

    def test_calendar_status_colour(self):
        self.client.force_login(self.stu)
        self.client.post(reverse("student_interview_quick", args=[self.iv.pk]), {"action": "attended"})
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse("admin_calendar")), "ev-attended")
