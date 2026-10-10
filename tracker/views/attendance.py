import logging

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from ..forms import (
    LeaveRequestForm,
)
from ..models import (
    Attendance,
    Group,
    GroupMembership,
    LeaveRequest,
)

logger = logging.getLogger(__name__)

from ..permissions import staff_required, student_required


def _attendance_owner(user):
    return user.data_owner


def _is_active_batch_student(user):
    return user.student_groups.filter(is_active=True).exists()


def _approved_leave_on(student, day):
    return LeaveRequest.objects.filter(
        student=student, status=LeaveRequest.APPROVED,
        start_date__lte=day, end_date__gte=day,
    ).first()


@student_required
@require_GET
def student_attendance(request):
    today = timezone.localdate()
    return render(request, "tracker/student/attendance.html", {
        "eligible": _is_active_batch_student(request.user),
        "today": today,
        "record": Attendance.objects.filter(student=request.user, date=today).first(),
        "leave_today": _approved_leave_on(request.user, today),
        "history": Attendance.objects.filter(student=request.user)[:30],
        "leaves": request.user.leave_requests.all()[:20],
        "leave_form": LeaveRequestForm(student=request.user),
    })


@student_required
@require_POST
def student_attendance_mark(request, action):
    if not _is_active_batch_student(request.user):
        messages.error(request, "Attendance is only for students in an active batch.")
        return redirect("student_attendance")
    now = timezone.now()
    today = timezone.localdate(now)
    if action == "in":
        if _approved_leave_on(request.user, today):
            messages.error(request, "You have approved leave today.")
            return redirect("student_attendance")
        _, created = Attendance.objects.get_or_create(
            student=request.user, date=today, defaults={"check_in": now}
        )
        if created:
            messages.success(request, f"Checked in at {timezone.localtime(now):%I:%M %p}.")
        else:
            messages.info(request, "You have already checked in today.")
    elif action == "out":
        with transaction.atomic():
            record = Attendance.objects.select_for_update().filter(
                student=request.user, date=today
            ).first()
            if record is None:
                messages.error(request, "Check in first.")
            elif record.check_out:
                messages.info(request, "You have already checked out today.")
            else:
                record.check_out = now
                record.save(update_fields=["check_out"])
                messages.success(request, f"Checked out at {timezone.localtime(now):%I:%M %p}.")
    return redirect("student_attendance")


@student_required
@require_POST
def student_leave_apply(request):
    form = LeaveRequestForm(request.POST, student=request.user)
    if form.is_valid():
        leave = form.save(commit=False)
        leave.student = request.user
        leave.save()
        messages.success(request, "Leave request sent. You'll see the decision here.")
    else:
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    return redirect("student_attendance")


@student_required
@require_POST
def student_leave_cancel(request, pk):
    leave = get_object_or_404(
        LeaveRequest, pk=pk, student=request.user, status=LeaveRequest.PENDING
    )
    leave.status = LeaveRequest.CANCELLED
    leave.save(update_fields=["status"])
    messages.success(request, "Leave request cancelled.")
    return redirect("student_attendance")


def _staff_batches(user):
    return Group.objects.filter(admin=_attendance_owner(user)).order_by("name")


@staff_required
@require_GET
def staff_attendance(request):
    batches = _staff_batches(request.user)
    active = batches.filter(is_active=True)
    day = parse_date(request.GET.get("date", "")) or timezone.localdate()
    batch_id = request.GET.get("batch", "")
    scope = active.filter(pk=batch_id) if batch_id.isdigit() else active
    memberships = GroupMembership.objects.filter(group__in=scope).select_related("student", "group")
    records = {a.student_id: a for a in Attendance.objects.filter(date=day)}
    on_leave = {
        l.student_id: l for l in LeaveRequest.objects.filter(
            status=LeaveRequest.APPROVED, start_date__lte=day, end_date__gte=day
        )
    }
    rows = []
    for m in sorted(memberships, key=lambda m: (m.group.name.lower(), m.student.username.lower())):
        record = records.get(m.student_id)
        if record:
            status = "Present"
        elif m.student_id in on_leave:
            status = "On leave"
        else:
            status = "Absent"
        rows.append({"student": m.student, "group": m.group, "record": record, "status": status})
    counts = {
        key: sum(r["status"] == label for r in rows)
        for key, label in (("Present", "Present"), ("On_leave", "On leave"), ("Absent", "Absent"))
    }
    return render(request, "tracker/staff/attendance.html", {
        "rows": rows, "counts": counts, "total": len(rows), "day": day,
        "batches": active, "selected_batch": batch_id,
        "is_today": day == timezone.localdate(),
    })


@staff_required
@require_GET
def staff_leaves(request):
    students = GroupMembership.objects.filter(group__in=_staff_batches(request.user)).values("student")
    leaves = LeaveRequest.objects.filter(student__in=students).select_related("student", "reviewed_by")
    status = request.GET.get("status", "pending")
    if status in {LeaveRequest.PENDING, LeaveRequest.APPROVED, LeaveRequest.REJECTED, LeaveRequest.CANCELLED}:
        leaves = leaves.filter(status=status)
    else:
        status = "all"
    page_obj = Paginator(leaves, 25).get_page(request.GET.get("page"))
    return render(request, "tracker/staff/leaves.html", {
        "page_obj": page_obj, "status": status,
        "pending_total": LeaveRequest.objects.filter(
            student__in=students, status=LeaveRequest.PENDING).count(),
        "today": timezone.localdate(),
    })


@staff_required
@require_POST
def staff_leave_review(request, pk, decision):
    if decision not in {"approve", "reject"}:
        raise PermissionDenied
    students = GroupMembership.objects.filter(group__in=_staff_batches(request.user)).values("student")
    leave = get_object_or_404(
        LeaveRequest, pk=pk, student__in=students, status=LeaveRequest.PENDING
    )
    leave.status = LeaveRequest.APPROVED if decision == "approve" else LeaveRequest.REJECTED
    leave.reviewed_by = request.user
    leave.reviewed_at = timezone.now()
    leave.review_note = request.POST.get("note", "").strip()[:300]
    leave.save()
    messages.success(request, f"Leave {leave.get_status_display().lower()} for {leave.student.username}.")
    return redirect("staff_leaves")
