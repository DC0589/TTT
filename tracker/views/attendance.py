from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from ..forms import LeaveRequestForm
from ..models import Attendance, Group, GroupMembership, LeaveRequest
from ..permissions import staff_required, student_required
from ..services import attendance as service


@student_required
@require_GET
def student_attendance(request):
    today = timezone.localdate()
    return render(request, "tracker/student/attendance.html", {
        "eligible": service.is_active_batch_student(request.user),
        "today": today,
        "record": Attendance.objects.filter(student=request.user, date=today).first(),
        "leave_today": service.approved_leave_on(request.user, today),
        "history": Attendance.objects.filter(student=request.user)[:30],
        "leaves": request.user.leave_requests.all()[:20],
        "leave_form": LeaveRequestForm(student=request.user),
    })


@student_required
@require_POST
def student_attendance_mark(request, action):
    if action not in {"in", "out"}:
        raise PermissionDenied
    try:
        if action == "in":
            record, created = service.check_in(request.user)
            if created:
                messages.success(request, f"Checked in at {timezone.localtime(record.check_in):%I:%M %p}.")
            else:
                messages.info(request, "You have already checked in today.")
        else:
            record, changed = service.check_out(request.user)
            if changed:
                messages.success(request, f"Checked out at {timezone.localtime(record.check_out):%I:%M %p}.")
            else:
                messages.info(request, "You have already checked out today.")
    except service.AttendanceError as exc:
        messages.error(request, str(exc))
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
    service.cancel_leave(leave)
    messages.success(request, "Leave request cancelled.")
    return redirect("student_attendance")


def _staff_batches(user):
    return Group.objects.filter(admin=user.data_owner).order_by("name")


def _staff_student_ids(user):
    return GroupMembership.objects.filter(group__admin=user.data_owner).values("student")


@staff_required
@require_GET
def staff_attendance(request):
    active = _staff_batches(request.user).filter(is_active=True)
    day = parse_date(request.GET.get("date", "")) or timezone.localdate()
    batch_id = request.GET.get("batch", "")
    memberships = service.batch_students(
        request.user.data_owner, batch_id=int(batch_id) if batch_id.isdigit() else None
    )
    rows = service.attendance_for_day(memberships, day)
    return render(request, "tracker/staff/attendance.html", {
        "rows": rows, "counts": service.summarise(rows), "total": len(rows), "day": day,
        "batches": active, "selected_batch": batch_id,
        "is_today": day == timezone.localdate(),
    })


@staff_required
@require_GET
def staff_leaves(request):
    students = _staff_student_ids(request.user)
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
    leave = get_object_or_404(
        LeaveRequest, pk=pk, student__in=_staff_student_ids(request.user), status=LeaveRequest.PENDING
    )
    service.review_leave(leave, request.user, decision == "approve", request.POST.get("note", ""))
    messages.success(request, f"Leave {leave.get_status_display().lower()} for {leave.student.username}.")
    return redirect("staff_leaves")
