import datetime

from django.test import TestCase
from django.utils import timezone

from .models import Attendance, AttendanceSettings, Group, GroupMembership, User
from .services.attendance import auto_close_stale, check_out


class AutoCheckoutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("checkout-admin", is_admin=True)
        cls.student = User.objects.create_user("checkout-student", is_student=True)
        group = Group.objects.create(name="Checkout batch", admin=cls.admin)
        GroupMembership.objects.create(group=group, student=cls.student)
        cls.day = datetime.date(2026, 11, 12)

    def at(self, hour, minute=0, day=None):
        return timezone.make_aware(datetime.datetime.combine(day or self.day, datetime.time(hour, minute)))

    def record(self, hour=9, minute=0, day=None, **kwargs):
        return Attendance.objects.create(
            student=self.student, date=day or self.day, check_in=self.at(hour, minute, day), **kwargs
        )

    def test_default_cutoff_is_six_pm_in_local_timezone(self):
        record = self.record()
        self.assertEqual(auto_close_stale(now=self.at(17, 59)), 0)
        self.assertEqual(auto_close_stale(now=self.at(18)), 1)
        record.refresh_from_db()
        self.assertEqual(record.check_out, self.at(18))
        self.assertTrue(record.auto_closed)
        self.assertEqual(auto_close_stale(now=self.at(19)), 0)

    def test_custom_cutoff_and_late_checkin(self):
        AttendanceSettings.objects.create(admin=self.admin, auto_close_at=datetime.time(23))
        record = self.record(23, 15)
        self.assertEqual(auto_close_stale(now=self.at(23, 10)), 0)
        self.assertEqual(auto_close_stale(now=self.at(23, 16)), 1)
        record.refresh_from_db()
        self.assertEqual(record.check_out, self.at(23, 15))

    def test_previous_day_and_manual_checkout_are_preserved(self):
        previous = self.day - datetime.timedelta(days=1)
        record = self.record(9, day=previous)
        self.assertEqual(auto_close_stale(now=self.at(9)), 1)
        record.refresh_from_db()
        self.assertEqual(record.check_out, self.at(18, day=previous))
        manual = self.record(check_out=self.at(17))
        self.assertEqual(auto_close_stale(now=self.at(19)), 0)
        manual.refresh_from_db()
        self.assertEqual(manual.check_out, self.at(17))
        self.assertFalse(manual.auto_closed)

    def test_checkout_after_cutoff_is_automatic_not_manual(self):
        record = self.record()
        result, changed = check_out(self.student, (17.0, 78.0, 5), now=self.at(19))
        self.assertTrue(changed)
        self.assertEqual(result.pk, record.pk)
        self.assertEqual(result.check_out, self.at(18))
        self.assertTrue(result.auto_closed)
        self.assertIsNone(result.check_out_lat)

    def test_checkout_before_cutoff_stays_manual(self):
        self.record()
        result, changed = check_out(self.student, (17.0, 78.0, 5), now=self.at(17, 30))
        self.assertTrue(changed)
        self.assertEqual(result.check_out, self.at(17, 30))
        self.assertFalse(result.auto_closed)
        self.assertEqual(result.check_out_lat, 17.0)

    def test_custom_cutoff_before_and_at_time(self):
        AttendanceSettings.objects.create(admin=self.admin, auto_close_at=datetime.time(23))
        record = self.record()
        self.assertEqual(auto_close_stale(now=self.at(22, 59)), 0)
        self.assertEqual(auto_close_stale(now=self.at(23)), 1)
        record.refresh_from_db()
        self.assertEqual(record.check_out, self.at(23))

    def test_student_filter_does_not_close_other_students(self):
        other = User.objects.create_user("checkout-other", is_student=True)
        group = Group.objects.get(admin=self.admin)
        GroupMembership.objects.create(group=group, student=other)
        self.record()
        other_record = Attendance.objects.create(student=other, date=self.day, check_in=self.at(9))
        self.assertEqual(auto_close_stale(student=self.student, now=self.at(19)), 1)
        other_record.refresh_from_db()
        self.assertIsNone(other_record.check_out)
