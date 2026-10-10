from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import Attendance, Group, GroupMembership, LeaveRequest, LoginLog, User

LOC = {"latitude": "17.385", "longitude": "78.486", "accuracy": "12"}


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
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.assertEqual(Attendance.objects.filter(student=self.student).count(), 1)
        self.client.post(reverse("student_attendance_mark", args=["out"]), LOC)
        self.assertIsNotNone(Attendance.objects.get(student=self.student).check_out)

    def test_student_outside_active_batch_cannot_check_in(self):
        self.client.force_login(self.outsider)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.assertFalse(Attendance.objects.exists())
        self.batch.is_active = False
        self.batch.save()
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
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


class LocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("adm2", password="pw-12345", is_admin=True)
        cls.student = User.objects.create_user("stu2", password="pw-12345", is_student=True)
        batch = Group.objects.create(name="B2", admin=cls.admin)
        GroupMembership.objects.create(group=batch, student=cls.student)

    def test_location_saved_on_check_in_and_out(self):
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.client.post(reverse("student_attendance_mark", args=["out"]), {**LOC, "latitude": "17.4"})
        rec = Attendance.objects.get(student=self.student)
        self.assertEqual((rec.check_in_lat, rec.check_in_lng), (17.385, 78.486))
        self.assertEqual(rec.check_out_lat, 17.4)
        self.assertIn("17.385000,78.486000", rec.check_in_map_url)

    def test_attendance_page_renders(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("student_attendance"))
        self.assertContains(response, "geo-form")

    def test_missing_or_invalid_location_rejected(self):
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]))
        self.client.post(reverse("student_attendance_mark", args=["in"]), {**LOC, "latitude": "999"})
        self.assertFalse(Attendance.objects.exists())


class LoginActivityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("adm3", password="pw-12345", is_admin=True)
        cls.hr = User.objects.create_user("hr3", password="pw-12345", is_hr=True, created_by=cls.admin)
        cls.student = User.objects.create_user("stu3", password="pw-12345", is_student=True)

    def test_login_and_logout_are_logged(self):
        self.assertTrue(self.client.login(username="stu3", password="pw-12345"))
        log = LoginLog.objects.get(user=self.student)
        self.assertIsNone(log.logged_out_at)
        self.client.logout()
        log.refresh_from_db()
        self.assertIsNotNone(log.logged_out_at)

    def test_online_list_shows_active_and_hides_stale(self):
        self.client.login(username="stu3", password="pw-12345")
        stu_client = self.client
        from django.test import Client
        staff = Client()
        staff.force_login(self.admin)
        page = staff.get(reverse("login_activity"))
        self.assertContains(page, "stu3")
        self.assertEqual(len(page.context["online"]), 2)
        LoginLog.objects.filter(user=self.student).update(last_seen=timezone.now() - timedelta(minutes=30))
        page = staff.get(reverse("login_activity"))
        self.assertEqual([l.user.username for l in page.context["online"]], ["adm3"])
        stu_client.logout()

    def test_hr_sees_only_students_and_students_forbidden(self):
        from django.test import Client
        LoginLog.objects.create(user=self.admin)
        LoginLog.objects.create(user=self.student)
        hr = Client()
        hr.force_login(self.hr)
        page = hr.get(reverse("login_activity"))
        names = {l.user.username for l in page.context["page_obj"]}
        self.assertNotIn("adm3", names)
        self.assertIn("stu3", names)
        stu = Client()
        stu.force_login(self.student)
        self.assertEqual(stu.get(reverse("login_activity")).status_code, 403)
