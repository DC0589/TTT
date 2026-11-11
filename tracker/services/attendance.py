import calendar
import datetime
import math
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ..models import Attendance, AttendanceSettings, GroupMembership, LeaveRequest, StudentBreak

PRESENT, ON_LEAVE, ABSENT = "Present", "On leave", "Absent"


class AttendanceError(Exception):
    """A rule was broken; the message is safe to show to the user."""


def is_active_batch_student(student):
    return student.student_groups.filter(is_active=True).exists()


def approved_leave_on(student, day):
    return LeaveRequest.objects.filter(
        student=student, status=LeaveRequest.APPROVED,
        start_date__lte=day, end_date__gte=day,
    ).first()


def parse_location(lat, lng, accuracy=None):
    """Validate browser geolocation values. Returns (lat, lng, accuracy)."""
    try:
        lat, lng = float(lat), float(lng)
        accuracy = float(accuracy) if accuracy not in (None, "") else None
    except (TypeError, ValueError):
        raise AttendanceError("Location is required. Allow location access in your browser and try again.")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise AttendanceError("Location looks invalid. Please try again.")
    return lat, lng, accuracy


def haversine_m(lat1, lng1, lat2, lng2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return int(2 * r * math.asin(math.sqrt(h)))


def settings_for(student):
    """The attendance rules of the admin who owns the student's active batch."""
    membership = (
        GroupMembership.objects.filter(student=student, group__is_active=True)
        .select_related("group").first()
    )
    if membership is None:
        return None
    obj, _ = AttendanceSettings.objects.get_or_create(admin_id=membership.group.admin_id)
    return obj


def _geofence(rules, location, action):
    """Returns (distance_m, outside). Raises when blocking is on and the student is too far."""
    if rules is None or not rules.geofence_enabled:
        return None, False
    distance = haversine_m(location[0], location[1], rules.centre_lat, rules.centre_lng)
    outside = distance > rules.radius_m
    if outside and rules.block_outside:
        raise AttendanceError(
            f"You are about {distance} m from the training centre. "
            f"Move within {rules.radius_m} m to check {action}."
        )
    return distance, outside


def check_in(student, location, now=None):
    """Record today's check-in. Returns (record, created)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    if not is_active_batch_student(student):
        raise AttendanceError("Attendance is only for students in an active batch.")
    if approved_leave_on(student, today):
        raise AttendanceError("You have approved leave today.")
    rules = settings_for(student)
    existing = Attendance.objects.filter(student=student, date=today).first()
    if existing:
        return existing, False
    distance, outside = _geofence(rules, location, "in")
    lat, lng, accuracy = location
    late = timezone.localtime(now).time() > rules.late_after
    return Attendance.objects.get_or_create(
        student=student, date=today,
        defaults={
            "check_in": now, "check_in_lat": lat, "check_in_lng": lng, "check_in_accuracy": accuracy,
            "is_late": late, "outside_geofence": outside, "distance_m": distance,
        },
    )


def check_out(student, location, now=None):
    """Record today's check-out. Returns (record, changed)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    if not is_active_batch_student(student):
        raise AttendanceError("Attendance is only for students in an active batch.")
    with transaction.atomic():
        record = Attendance.objects.select_for_update().filter(student=student, date=today).first()
        if record is None:
            raise AttendanceError("Check in first.")
        if record.check_out:
            return record, False
        _, outside = _geofence(settings_for(student), location, "out")
        record.check_out = now
        record.check_out_lat, record.check_out_lng, record.check_out_accuracy = location
        record.check_out_outside_geofence = outside
        record.save(update_fields=[
            "check_out", "check_out_lat", "check_out_lng", "check_out_accuracy", "check_out_outside_geofence",
        ])
    return record, True


def needs_check_in(student, now=None):
    """True when an active-batch student has neither checked in nor got approved leave today."""
    today = timezone.localdate(now or timezone.now())
    if not is_active_batch_student(student):
        return False
    if Attendance.objects.filter(student=student, date=today).exists():
        return False
    return approved_leave_on(student, today) is None


def auto_close_stale(owner=None, now=None):
    """Close open records from earlier days at the batch's auto-close time. Returns the count."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    open_records = Attendance.objects.filter(check_out__isnull=True, date__lt=today)
    if owner is not None:
        open_records = open_records.filter(student__student_groups__admin=owner)
    closed = 0
    for record in open_records.distinct():
        rules = settings_for(record.student)
        close_time = rules.auto_close_at if rules else datetime.time(18, 0)
        closing = timezone.make_aware(datetime.datetime.combine(record.date, close_time))
        record.check_out = max(closing, record.check_in)
        record.auto_closed = True
        record.save(update_fields=["check_out", "auto_closed"])
        closed += 1
    return closed


def review_leave(leave, reviewer, approve, note=""):
    leave.status = LeaveRequest.APPROVED if approve else LeaveRequest.REJECTED
    leave.reviewed_by = reviewer
    leave.reviewed_at = timezone.now()
    leave.review_note = (note or "").strip()[:300]
    leave.save()
    return leave


def cancel_leave(leave):
    leave.status = LeaveRequest.CANCELLED
    leave.save(update_fields=["status"])
    return leave


def batch_students(owner, active_only=True, batch_id=None):
    """Memberships for an admin's batches, optionally limited to active ones or one batch."""
    memberships = GroupMembership.objects.filter(group__admin=owner).select_related("student", "group")
    if active_only:
        memberships = memberships.filter(group__is_active=True)
    if batch_id:
        memberships = memberships.filter(group_id=batch_id)
    return memberships


@dataclass
class DayRow:
    student: object
    group: object
    record: object
    status: str


def attendance_for_day(memberships, day):
    """One row per student with their status on day, sorted by batch then username."""
    memberships = list(memberships)
    ids = [m.student_id for m in memberships]
    records = {
        a.student_id: a for a in Attendance.objects.filter(date=day, student_id__in=ids)
        .select_related("manually_checked_in_by", "late_marked_by")
    }
    on_leave = set(
        LeaveRequest.objects.filter(
            status=LeaveRequest.APPROVED, start_date__lte=day, end_date__gte=day, student_id__in=ids
        ).values_list("student_id", flat=True)
    )
    owner_ids = {m.group.admin_id for m in memberships}
    break_owners = set(StudentBreak.objects.filter(
        admin_id__in=owner_ids, start_date__lte=day, end_date__gte=day,
    ).values_list("admin_id", flat=True))
    today = timezone.localdate()
    rows = []
    for m in sorted(memberships, key=lambda m: (m.group.name.lower(), m.student.username.lower())):
        record = records.get(m.student_id)
        if record:
            status = PRESENT
        elif m.student_id in on_leave:
            status = ON_LEAVE
        elif (day.weekday() not in WORKING_WEEKDAYS or m.group.admin_id in break_owners
              or day < timezone.localtime(m.joined_at).date()):
            status = "Not expected"
        elif day > today:
            status = "Upcoming"
        else:
            status = ABSENT
        rows.append(DayRow(m.student, m.group, record, status))
    return rows


def leave_calendar(owner, memberships, year, month, today=None, leave_student_ids=None):
    """Return each month's leave applications, late check-ins and past weekday absences."""
    today = today or timezone.localdate()
    start = datetime.date(year, month, 1)
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    memberships = list(memberships)
    students = {}
    for membership in memberships:
        joined = timezone.localtime(membership.joined_at).date()
        entry = students.setdefault(membership.student_id, (membership.student, joined))
        if joined < entry[1]:
            students[membership.student_id] = (membership.student, joined)
    ids = list(students)
    if leave_student_ids is None:
        leave_student_ids = ids
    records = {
        (record.student_id, record.date): record
        for record in Attendance.objects.filter(student_id__in=ids, date__range=(start, end))
        .select_related("student")
    }
    leaves = list(LeaveRequest.objects.filter(
        student_id__in=leave_student_ids, start_date__lte=end, end_date__gte=start,
    ).select_related("student"))
    breaks = list(StudentBreak.objects.filter(admin=owner, start_date__lte=end, end_date__gte=start))
    result = []
    for number in range(1, end.day + 1):
        day = start.replace(day=number)
        day_leaves = [leave for leave in leaves if leave.start_date <= day <= leave.end_date]
        approved = {leave.student_id for leave in day_leaves if leave.status == LeaveRequest.APPROVED}
        late = [record for (_, date), record in records.items() if date == day and record.is_late]
        absent = []
        is_break = any(item.start_date <= day <= item.end_date for item in breaks)
        if day < today and day.weekday() in WORKING_WEEKDAYS and not is_break:
            absent = [
                student for student_id, (student, joined) in students.items()
                if joined <= day and student_id not in approved and (student_id, day) not in records
            ]
        result.append({"date": day, "leaves": day_leaves, "late": late, "absent": absent, "is_break": is_break})
    return result


def summarise(rows):
    return {
        "Present": sum(r.status == PRESENT for r in rows),
        "On_leave": sum(r.status == ON_LEAVE for r in rows),
        "Absent": sum(r.status == ABSENT for r in rows),
    }


WORKING_WEEKDAYS = range(0, 5)  # Monday to Friday


def working_days(year, month, until=None):
    first = datetime.date(year, month, 1)
    end = datetime.date(year, month, calendar.monthrange(year, month)[1])
    if until is not None:
        end = min(end, until)
    if end < first:
        return []
    return [
        first + datetime.timedelta(days=i) for i in range((end - first).days + 1)
        if (first + datetime.timedelta(days=i)).weekday() in WORKING_WEEKDAYS
    ]


@dataclass
class ReportRow:
    student: object
    group: object
    present: int
    late: int
    leave: int
    absent: int
    working: int
    percent: float
    outside: int


def monthly_report(memberships, year, month, today=None):
    """Attendance figures for working days, excluding breaks and days before joining."""
    today = today or timezone.localdate()
    memberships = list(memberships)
    days = working_days(year, month, until=today)
    ids = [m.student_id for m in memberships]
    records = {}
    for rec in Attendance.objects.filter(student_id__in=ids, date__in=days):
        records.setdefault(rec.student_id, []).append(rec)
    leaves = {}
    for leave in LeaveRequest.objects.filter(
        student_id__in=ids, status=LeaveRequest.APPROVED,
        start_date__lte=datetime.date(year, month, calendar.monthrange(year, month)[1]),
        end_date__gte=datetime.date(year, month, 1),
    ):
        leaves.setdefault(leave.student_id, []).append(leave)
    breaks = {}
    for item in StudentBreak.objects.filter(
        admin_id__in={m.group.admin_id for m in memberships},
        start_date__lte=datetime.date(year, month, calendar.monthrange(year, month)[1]),
        end_date__gte=datetime.date(year, month, 1),
    ):
        breaks.setdefault(item.admin_id, []).append(item)
    rows = []
    for m in sorted(memberships, key=lambda m: (m.group.name.lower(), m.student.username.lower())):
        joined = timezone.localtime(m.joined_at).date()
        owner_breaks = breaks.get(m.group.admin_id, [])
        eligible_days = {
            day for day in days if day >= joined
            and not any(item.start_date <= day <= item.end_date for item in owner_breaks)
        }
        recs = [r for r in records.get(m.student_id, []) if r.date in eligible_days]
        present_days = {r.date for r in recs}
        leave_days = {
            d for leave in leaves.get(m.student_id, []) for d in eligible_days
            if leave.start_date <= d <= leave.end_date and d not in present_days
        }
        if today not in present_days and today not in leave_days:
            eligible_days.discard(today)
        expected = len(eligible_days) - len(leave_days)
        absent = max(expected - len(present_days), 0)
        percent = round(100 * len(present_days) / expected, 1) if expected > 0 else 0.0
        rows.append(ReportRow(
            m.student, m.group, len(present_days), sum(r.is_late for r in recs), len(leave_days),
            absent, len(eligible_days), percent,
            sum(r.outside_geofence or r.check_out_outside_geofence for r in recs),
        ))
    return rows


def batch_summary(rows):
    groups = {}
    for r in rows:
        g = groups.setdefault(r.group.pk, {"group": r.group, "students": 0, "present": 0, "expected": 0, "late": 0})
        g["students"] += 1
        g["present"] += r.present
        g["expected"] += r.working - r.leave
        g["late"] += r.late
    for g in groups.values():
        g["percent"] = round(100 * g["present"] / g["expected"], 1) if g["expected"] else 0.0
    return list(groups.values())
