from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Attendance, Group, GroupMembership, LeaveRequest, User


class AttendanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("adm", password="pw-12345", is_admin=True)
        cls.hr = User.objects.create_user("hr1", password="pw-12345", is_hr=True, created_by=cls.admin)
        cls.student = User.objects.create_user("stu", password="pw-12345", is_student=True)
        cls.outsider = User.objects.create_user("out", password="pw-12345", is_student=True)
        cls.batch = Group.objects.create(name="B1", admin=cls.admin)
        GroupMembership.objects.create(group=cls.batch, student=cls.student)

    def test_check_in_once_then_check_out(self):
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]))
        self.client.post(reverse("student_attendance_mark", args=["in"]))
        self.assertEqual(Attendance.objects.filter(student=self.student).count(), 1)
        self.client.post(reverse("student_attendance_mark", args=["out"]))
        self.assertIsNotNone(Attendance.objects.get(student=self.student).check_out)

    def test_student_outside_active_batch_cannot_check_in(self):
        self.client.force_login(self.outsider)
        self.client.post(reverse("student_attendance_mark", args=["in"]))
        self.assertFalse(Attendance.objects.exists())
        self.batch.is_active = False
        self.batch.save()
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]))
        self.assertFalse(Attendance.objects.exists())

    def test_leave_overlap_rejected_and_hr_can_approve(self):
        day = timezone.localdate() + timedelta(days=2)
        self.client.force_login(self.student)
        data = {"start_date": day, "end_date": day, "reason": "family function"}
        self.client.post(reverse("student_leave_apply"), data)
        self.client.post(reverse("student_leave_apply"), data)
        self.assertEqual(LeaveRequest.objects.count(), 1)

        leave = LeaveRequest.objects.get()
        self.client.force_login(self.hr)
        self.client.post(reverse("staff_leave_review", args=[leave.pk, "approve"]), {"note": "ok"})
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveRequest.APPROVED)
        self.assertEqual(leave.reviewed_by, self.hr)

    def test_students_cannot_open_staff_pages_or_review_leave(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("staff_leaves")).status_code, 403)
        self.assertEqual(self.client.get(reverse("staff_attendance")).status_code, 403)

    def test_other_admins_leave_is_not_reviewable(self):
        other = User.objects.create_user("adm2", password="pw-12345", is_admin=True)
        leave = LeaveRequest.objects.create(
            student=self.student, start_date=timezone.localdate(),
            end_date=timezone.localdate(), reason="appointment",
        )
        self.client.force_login(other)
        response = self.client.post(reverse("staff_leave_review", args=[leave.pk, "approve"]))
        self.assertEqual(response.status_code, 404)


@override_settings(THROTTLE_ENABLED=True)
class ThrottleTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_repeated_failed_logins_are_blocked(self):
        for _ in range(8):
            self.client.post(reverse("login"), {"username": "nobody", "password": "bad"})
        response = self.client.post(reverse("login"), {"username": "nobody", "password": "bad"})
        self.assertEqual(response.status_code, 429)


class DataOwnerTests(TestCase):
    def test_hr_resolves_to_creating_admin(self):
        admin = User.objects.create_user("a", password="x", is_admin=True)
        hr = User.objects.create_user("h", password="x", is_hr=True, created_by=admin)
        self.assertEqual(hr.data_owner, admin)
        self.assertEqual(admin.data_owner, admin)
