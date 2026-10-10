import csv
import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from ..forms import AttendanceSettingsForm, LeaveRequestForm
from ..models import Attendance, AttendanceSettings, Group, GroupMembership, LeaveRequest
from ..permissions import admin_required, staff_required, student_required
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
        location = service.parse_location(
            request.POST.get("latitude"), request.POST.get("longitude"), request.POST.get("accuracy")
        )
        if action == "in":
            record, created = service.check_in(request.user, location)
            if created:
                messages.success(request, f"Checked in at {timezone.localtime(record.check_in):%I:%M %p}.")
            else:
                messages.info(request, "You have already checked in today.")
        else:
            record, changed = service.check_out(request.user, location)
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


def _month_from(request):
    today = timezone.localdate()
    raw = request.GET.get("month", "")
    try:
        year, month = (int(x) for x in raw.split("-"))
        datetime.date(year, month, 1)
    except (ValueError, TypeError):
        year, month = today.year, today.month
    return year, month


@staff_required
@require_GET
def staff_attendance_report(request):
    owner = request.user.data_owner
    service.auto_close_stale(owner)
    year, month = _month_from(request)
    batch_id = request.GET.get("batch", "")
    memberships = list(service.batch_students(
        owner, active_only=False, batch_id=int(batch_id) if batch_id.isdigit() else None
    ))
    rows = service.monthly_report(memberships, year, month)
    if request.GET.get("export") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="attendance-{year}-{month:02d}.csv"'
        writer = csv.writer(response)
        writer.writerow(["Student", "Batch", "Working days", "Present", "Late", "Leave", "Absent", "Attendance %", "Outside location"])
        for r in rows:
            name = r.student.get_full_name() or r.student.username
            writer.writerow([name, r.group.name, r.working, r.present, r.late, r.leave, r.absent, r.percent, r.outside])
        return response
    return render(request, "tracker/staff/attendance_report.html", {
        "rows": rows, "summary": service.batch_summary(rows),
        "month_value": f"{year}-{month:02d}", "month_label": datetime.date(year, month, 1),
        "batches": _staff_batches(request.user), "selected_batch": batch_id,
    })


@admin_required
def staff_attendance_settings(request):
    settings_obj, _ = AttendanceSettings.objects.get_or_create(admin=request.user)
    form = AttendanceSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Attendance rules saved.")
        return redirect("staff_attendance_settings")
    return render(request, "tracker/staff/attendance_settings.html", {"form": form})
