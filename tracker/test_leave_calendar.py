import datetime
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Attendance, Group, GroupMembership, LeaveRequest, StudentBreak, User
from .services import attendance as service
from .views.attendance import staff_attendance, staff_leave_calendar, staff_mark_late


class LeaveCalendarTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user("calendar-admin", is_admin=True)
        cls.hr = User.objects.create_user("calendar-hr", is_hr=True, created_by=cls.admin)
        cls.other_admin = User.objects.create_user("calendar-other", is_admin=True)
        cls.student = User.objects.create_user("calendar-student", is_student=True)
        cls.other_student = User.objects.create_user("calendar-outsider", is_student=True)
        cls.group = Group.objects.create(name="Calendar batch", admin=cls.admin)
        cls.membership = GroupMembership.objects.create(group=cls.group, student=cls.student)
        other_group = Group.objects.create(name="Other batch", admin=cls.other_admin)
        GroupMembership.objects.create(group=other_group, student=cls.other_student)
        GroupMembership.objects.filter(pk=cls.membership.pk).update(
            joined_at=timezone.make_aware(datetime.datetime(2025, 1, 1))
        )

    def request(self, method, url, data, user):
        request = getattr(RequestFactory(), method)(url, data)
        request.user = user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request

    def test_calendar_tracks_all_leave_statuses_without_excusing_pending(self):
        for day, status in [(6, "pending"), (7, "approved"), (8, "rejected"), (9, "cancelled")]:
            LeaveRequest.objects.create(student=self.student, start_date=datetime.date(2025, 1, day),
                                        end_date=datetime.date(2025, 1, day), status=status, reason="test")
        StudentBreak.objects.create(admin=self.admin, name="Break",
                                    start_date=datetime.date(2025, 1, 10), end_date=datetime.date(2025, 1, 10))
        Attendance.objects.create(student=self.student, date=datetime.date(2025, 1, 3),
                                  check_in=timezone.make_aware(datetime.datetime(2025, 1, 3, 11)),
                                  is_late=True)
        days = service.leave_calendar(self.admin, service.batch_students(self.admin), 2025, 1,
                                      today=datetime.date(2025, 1, 11))
        self.assertEqual(days[2]["late"][0].student, self.student)
        for day in (6, 8, 9):
            self.assertEqual(days[day - 1]["absent"], [self.student])
        self.assertFalse(days[6]["absent"])
        self.assertTrue(days[9]["is_break"])
        self.assertFalse(days[9]["absent"])
        self.assertFalse(days[3]["absent"])  # Saturday
        self.assertEqual([days[d - 1]["leaves"][0].status for d in (6, 7, 8, 9)],
                         ["pending", "approved", "rejected", "cancelled"])
        self.assertNotIn(self.other_student, days[5]["absent"])

    def test_join_dates_and_future_days_are_not_absent(self):
        GroupMembership.objects.filter(pk=self.membership.pk).update(
            joined_at=timezone.make_aware(datetime.datetime(2025, 1, 8))
        )
        days = service.leave_calendar(self.admin, service.batch_students(self.admin), 2025, 1,
                                      today=datetime.date(2025, 1, 9))
        self.assertFalse(days[5]["absent"])
        self.assertEqual(days[7]["absent"], [self.student])
        self.assertFalse(days[8]["absent"])
        self.assertFalse(days[9]["absent"])

    def test_calendar_view_access_and_rendering(self):
        url = reverse("staff_leave_calendar")
        request = self.request("get", url, {"month": "2025-01"}, self.hr)
        response = staff_leave_calendar(request)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Leave calendar", response.content)
        with self.assertRaises(PermissionDenied):
            staff_leave_calendar(self.request("get", url, {}, self.student))

    def test_calendar_includes_leave_in_inactive_batches(self):
        self.group.is_active = False
        self.group.save()
        LeaveRequest.objects.create(student=self.student, start_date=datetime.date(2025, 1, 6),
                                    end_date=datetime.date(2025, 1, 6), reason="test")
        request = self.request("get", reverse("staff_leave_calendar"), {"month": "2025-01"}, self.hr)
        response = staff_leave_calendar(request)
        self.assertIn(b"calendar-student", response.content)
        self.assertIn(b">Pending</span>", response.content)

    def test_calendar_multiday_leave_and_tenant_isolation(self):
        LeaveRequest.objects.create(student=self.student, start_date=datetime.date(2024, 12, 31),
                                    end_date=datetime.date(2025, 1, 2), status=LeaveRequest.APPROVED,
                                    reason="test")
        LeaveRequest.objects.create(student=self.other_student, start_date=datetime.date(2025, 1, 1),
                                    end_date=datetime.date(2025, 1, 1), reason="private")
        request = self.request("get", reverse("staff_leave_calendar"), {"month": "2025-01"}, self.hr)
        content = staff_leave_calendar(request).content.decode()
        self.assertEqual(content.count('<li>calendar-student <span class="status-badge status-selected">Approved</span></li>'), 2)
        self.assertEqual(content.count(">Approved</span>"), 2)
        self.assertNotIn("calendar-outsider", content)

    def test_calendar_navigation_and_selected_day(self):
        request = self.request("get", reverse("staff_leave_calendar"),
                               {"month": "2025-01", "batch": str(self.group.pk), "day": "2025-01-06"},
                               self.hr)
        content = staff_leave_calendar(request).content.decode()
        self.assertIn("month=2024-12&amp;batch=", content)
        self.assertIn("month=2025-02&amp;batch=", content)
        self.assertIn('id="day-2025-01-06" open', content)
        self.assertIn("Absent student-days", content)

    def test_monthly_report_excludes_breaks_and_today_without_checkin(self):
        GroupMembership.objects.filter(pk=self.membership.pk).update(
            joined_at=timezone.make_aware(datetime.datetime(2025, 1, 6))
        )
        StudentBreak.objects.create(admin=self.admin, name="Break",
                                    start_date=datetime.date(2025, 1, 7), end_date=datetime.date(2025, 1, 7))
        row = service.monthly_report(service.batch_students(self.admin), 2025, 1,
                                     today=datetime.date(2025, 1, 9))[0]
        self.assertEqual((row.working, row.absent), (2, 2))

    def mark(self, user, student, day, **data):
        url = reverse("staff_mark_late", args=[student.pk])
        return staff_mark_late(self.request("post", url, {"date": day.isoformat(), **data}, user), student.pk)

    def test_hr_creates_late_checkin_and_can_correct_existing_record(self):
        day = timezone.localdate() - datetime.timedelta(days=1)
        self.mark(self.hr, self.student, day, action="late", check_in_time="11:15")
        record = Attendance.objects.get(student=self.student, date=day)
        self.assertTrue(record.is_late)
        self.assertEqual(timezone.localtime(record.check_in).time(), datetime.time(11, 15))
        self.assertEqual(record.manually_checked_in_by, self.hr)
        self.assertIsNone(record.check_in_lat)
        self.mark(self.hr, self.student, day, action="on_time")
        record.refresh_from_db()
        self.assertFalse(record.is_late)
        self.assertEqual(record.late_marked_by, self.hr)
        request = self.request("get", reverse("staff_attendance"), {"date": day.isoformat()}, self.hr)
        response = staff_attendance(request)
        self.assertIn(b"Mark late", response.content)
        self.assertIn(b"Manual: calendar-hr", response.content)

    def test_hr_cannot_create_for_approved_leave_or_future(self):
        day = timezone.localdate() - datetime.timedelta(days=1)
        LeaveRequest.objects.create(student=self.student, start_date=day, end_date=day,
                                    status=LeaveRequest.APPROVED, reason="test")
        self.mark(self.hr, self.student, day, action="late", check_in_time="11:15")
        self.mark(self.hr, self.student, timezone.localdate() + datetime.timedelta(days=1),
                  action="late", check_in_time="11:15")
        self.assertFalse(Attendance.objects.filter(student=self.student).exists())

    def test_permissions_and_invalid_time(self):
        day = timezone.localdate() - datetime.timedelta(days=1)
        with self.assertRaises(Http404):
            self.mark(self.hr, self.other_student, day, action="late", check_in_time="11:15")
        with self.assertRaises(PermissionDenied):
            self.mark(self.student, self.student, day, action="late", check_in_time="11:15")
        self.mark(self.hr, self.student, day, action="late", check_in_time="25:00")
        self.assertFalse(Attendance.objects.exists())

    def test_multiple_batch_memberships_produce_one_calendar_absence(self):
        extra_group = Group.objects.create(name="Another batch", admin=self.admin)
        extra = GroupMembership.objects.create(group=extra_group, student=self.student)
        GroupMembership.objects.filter(pk=extra.pk).update(joined_at=self.membership.joined_at)
        days = service.leave_calendar(self.admin, service.batch_students(self.admin), 2025, 1,
                                      today=datetime.date(2025, 1, 7))
        self.assertEqual(days[5]["absent"], [self.student])
        self.mark(self.hr, self.student, timezone.localdate() - datetime.timedelta(days=1),
                  action="late", check_in_time="11:15")
        self.assertEqual(Attendance.objects.filter(student=self.student).count(), 1)
