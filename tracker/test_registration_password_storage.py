from unittest.mock import patch

from django.contrib.auth.hashers import check_password
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from tracker.models import StudentRegistrationRequest, User
from tracker.views.auth import register
from tracker.views.registrations import registration_approve


@override_settings(THROTTLE_ENABLED=False)
class RegistrationPasswordStorageTests(TestCase):
    def request(self, name, data, user=None, args=None):
        request = RequestFactory().post(reverse(name, args=args), data, HTTP_HOST="localhost")
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    @patch("tracker.views.auth.send_registration_otp", return_value=True)
    @patch("tracker.views.registrations.send_notification", return_value=True)
    def test_password_is_retained_and_user_can_log_in_after_approval(self, notify, send_otp):
        password = "Example-pass-72!"
        response = register(self.request("register", {
            "username": "example-student", "email": "example@example.test",
            "mobile_number": "9876543210", "graduation": "B.Tech",
            "department": "CSE", "hometown": "Hyderabad", "parent_name": "Parent",
            "parent_mobile_number": "9876543211", "skills": "Python",
            "password1": password, "password2": password,
        }))
        self.assertEqual(response.status_code, 302)
        registration = StudentRegistrationRequest.objects.get(username="example-student")
        self.assertEqual(registration.raw_password, password)
        self.assertTrue(check_password(password, registration.password_hash))
        registration.status = StudentRegistrationRequest.AWAITING_APPROVAL
        registration.save(update_fields=["status"])
        admin = User.objects.create_user("test-admin", is_admin=True)
        response = registration_approve(
            self.request("registration_approve", {}, admin, [registration.pk]), registration.pk
        )
        self.assertEqual(response.status_code, 302)
        registration.refresh_from_db()
        self.assertEqual(registration.raw_password, password)
        self.assertEqual(registration.password_hash, "")
        self.assertTrue(User.objects.get(username="example-student").check_password(password))
        send_otp.assert_called_once()
        notify.assert_called_once()
