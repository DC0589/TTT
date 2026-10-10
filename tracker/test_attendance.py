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


@override_settings(CHECKIN_GATE=True)
class RulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("adm4", password="pw-12345", is_admin=True)
        cls.hr = User.objects.create_user("hr4", password="pw-12345", is_hr=True, created_by=cls.admin)
        cls.student = User.objects.create_user("stu4", password="pw-12345", is_student=True)
        cls.batch = Group.objects.create(name="B4", admin=cls.admin)
        GroupMembership.objects.create(group=cls.batch, student=cls.student)

    def _rules(self, **kw):
        from .models import AttendanceSettings
        AttendanceSettings.objects.update_or_create(admin=self.admin, defaults=kw)

    def test_gate_redirects_until_check_in(self):
        self.client.force_login(self.student)
        response = self.client.get(reverse("student_interviews"))
        self.assertRedirects(response, reverse("student_attendance"))
        self.assertEqual(self.client.get(reverse("student_attendance")).status_code, 200)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.assertEqual(self.client.get(reverse("student_interviews")).status_code, 200)

    def test_gate_skipped_on_approved_leave_and_for_non_batch_users(self):
        today = timezone.localdate()
        LeaveRequest.objects.create(
            student=self.student, start_date=today, end_date=today, reason="medical",
            status=LeaveRequest.APPROVED,
        )
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("student_interviews")).status_code, 200)
        loner = User.objects.create_user("loner", password="pw-12345", is_student=True)
        self.client.force_login(loner)
        self.assertEqual(self.client.get(reverse("student_interviews")).status_code, 200)

    def test_geofence_flags_or_blocks(self):
        self._rules(centre_lat=17.385, centre_lng=78.486, radius_m=100)
        far = {"latitude": "17.40", "longitude": "78.50", "accuracy": "5"}
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]), far)
        rec = Attendance.objects.get(student=self.student)
        self.assertTrue(rec.outside_geofence)
        self.assertGreater(rec.distance_m, 100)
        rec.delete()
        self._rules(block_outside=True)
        self.client.post(reverse("student_attendance_mark", args=["in"]), far)
        self.assertFalse(Attendance.objects.exists())
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.assertTrue(Attendance.objects.exists())
        self.assertFalse(Attendance.objects.get().outside_geofence)

    def test_late_flag(self):
        from datetime import time
        self._rules(late_after=time(0, 0))
        self.client.force_login(self.student)
        self.client.post(reverse("student_attendance_mark", args=["in"]), LOC)
        self.assertTrue(Attendance.objects.get().is_late)

    def test_auto_close_stale(self):
        from .services import attendance as svc
        yesterday = timezone.localdate() - timedelta(days=1)
        Attendance.objects.create(student=self.student, date=yesterday, check_in=timezone.now() - timedelta(days=1))
        self.assertEqual(svc.auto_close_stale(), 1)
        rec = Attendance.objects.get()
        self.assertTrue(rec.auto_closed)
        self.assertIsNotNone(rec.check_out)
        self.assertEqual(svc.auto_close_stale(), 0)

    def test_monthly_report_and_csv(self):
        from datetime import date

        from .services import attendance as svc
        days = svc.working_days(2026, 3)
        Attendance.objects.create(student=self.student, date=days[0], check_in=timezone.now(), is_late=True)
        Attendance.objects.create(student=self.student, date=days[1], check_in=timezone.now())
        LeaveRequest.objects.create(
            student=self.student, start_date=days[2], end_date=days[3], reason="trip",
            status=LeaveRequest.APPROVED,
        )
        memberships = list(svc.batch_students(self.admin))
        row = svc.monthly_report(memberships, 2026, 3, today=date(2026, 3, 31))[0]
        self.assertEqual((row.present, row.late, row.leave), (2, 1, 2))
        self.assertEqual(row.working, len(days))
        self.assertAlmostEqual(row.percent, round(100 * 2 / (len(days) - 2), 1))
        self.client.force_login(self.hr)
        page = self.client.get(reverse("staff_attendance_report"), {"month": "2026-03"})
        self.assertContains(page, "Monthly report")
        csv_response = self.client.get(reverse("staff_attendance_report"), {"month": "2026-03", "export": "csv"})
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertIn("stu4", csv_response.content.decode())

    def test_settings_admin_only_and_validated(self):
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse("staff_attendance_settings")).status_code, 403)
        self.client.force_login(self.admin)
        bad = self.client.post(reverse("staff_attendance_settings"), {
            "centre_lat": "17.3", "centre_lng": "", "radius_m": 200, "late_after": "10:00", "auto_close_at": "18:00",
        })
        self.assertEqual(bad.status_code, 200)
        ok = self.client.post(reverse("staff_attendance_settings"), {
            "centre_lat": "17.3", "centre_lng": "78.4", "radius_m": 150, "late_after": "09:30", "auto_close_at": "18:00",
        })
        self.assertEqual(ok.status_code, 302)
        self.assertEqual(self.admin.attendance_settings.radius_m, 150)

    def test_cron_close_requires_secret(self):
        self.assertEqual(self.client.get(reverse("cron_close_attendance")).status_code, 403)
