from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ..models import Attendance, GroupMembership, LeaveRequest

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


def check_in(student, now=None):
    """Record today's check-in. Returns (record, created)."""
    now = now or timezone.now()
    today = timezone.localdate(now)
    if not is_active_batch_student(student):
        raise AttendanceError("Attendance is only for students in an active batch.")
    if approved_leave_on(student, today):
        raise AttendanceError("You have approved leave today.")
    return Attendance.objects.get_or_create(
        student=student, date=today, defaults={"check_in": now}
    )


def check_out(student, now=None):
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
        record.check_out = now
        record.save(update_fields=["check_out"])
    return record, True


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
    ids = [m.student_id for m in memberships]
    records = {a.student_id: a for a in Attendance.objects.filter(date=day, student_id__in=ids)}
    on_leave = set(
        LeaveRequest.objects.filter(
            status=LeaveRequest.APPROVED, start_date__lte=day, end_date__gte=day, student_id__in=ids
        ).values_list("student_id", flat=True)
    )
    rows = []
    for m in sorted(memberships, key=lambda m: (m.group.name.lower(), m.student.username.lower())):
        record = records.get(m.student_id)
        status = PRESENT if record else ON_LEAVE if m.student_id in on_leave else ABSENT
        rows.append(DayRow(m.student, m.group, record, status))
    return rows


def summarise(rows):
    return {
        "Present": sum(r.status == PRESENT for r in rows),
        "On_leave": sum(r.status == ON_LEAVE for r in rows),
        "Absent": sum(r.status == ABSENT for r in rows),
    }
