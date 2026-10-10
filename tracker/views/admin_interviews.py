import csv
import logging
from datetime import date

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from ..forms import (
    InterviewAdminNotesForm,
)
from ..models import (
    Group,
    Interview,
    InterviewNoteReply,
    InterviewRound,
    InterviewStatus,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required, staff_required, student_required
from .interviews import _own_interview
from .mock import _csv_safe


def _filtered_interviews(request):
    owner = request.user.data_owner
    interviews = (
        Interview.objects.filter(group__admin=owner)
        .select_related("student", "group", "status")
        .prefetch_related("rounds")
    )
    params = request.GET
    batches = list(Group.objects.filter(admin=owner).order_by("name"))
    filters = {
        "batch": params.get("batch", "").strip(),
        "company": params.get("company", "").strip()[:100],
        "student": params.get("student", "").strip()[:100],
        "status": params.get("status", "").strip(),
        "type": params.get("type", "").strip(),
        "date_from": params.get("date_from", "").strip(),
        "date_to": params.get("date_to", "").strip(),
    }
    if filters["batch"].isdigit():
        interviews = interviews.filter(group_id=int(filters["batch"]))
    if filters["company"]:
        interviews = interviews.filter(company_name__icontains=filters["company"])
    if filters["student"]:
        interviews = interviews.filter(student__username__icontains=filters["student"])
    if filters["status"] == "in-progress":
        interviews = interviews.filter(status__isnull=True)
    elif filters["status"] in {"selected", "not-selected"}:
        interviews = interviews.filter(status__final_status=filters["status"])
    if filters["type"] in dict(Interview.TYPE_CHOICES):
        interviews = interviews.filter(interview_type=filters["type"])
    for key, lookup in (("date_from", "date_of_interview__gte"), ("date_to", "date_of_interview__lte")):
        if filters[key]:
            try:
                interviews = interviews.filter(**{lookup: date.fromisoformat(filters[key])})
            except ValueError:
                filters[key] = ""
    return interviews, filters, batches


@staff_required
def admin_interviews(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    interviews, filters, batches = _filtered_interviews(request)
    query = request.GET.copy()
    query.pop("view", None)
    return render(request, "tracker/admin/interviews.html", {
        "interviews": interviews,
        "view_mode": view_mode,
        "filters": filters,
        "batches": batches,
        "type_choices": Interview.TYPE_CHOICES,
        "filter_query": query.urlencode(),
        "has_filters": any(filters.values()),
    })


@staff_required
@require_GET
def admin_interviews_export(request):
    interviews, _filters, _batches = _filtered_interviews(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="interviews.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Date", "Time", "Student", "Batch", "Company", "Role", "Type", "Result", "Attendance",
        "Rounds cleared", "HR name", "HR contact number", "HR email", "Job link",
    ])
    for iv in interviews.order_by("-date_of_interview", "-time_of_interview"):
        writer.writerow([_csv_safe(x) for x in [
            iv.date_of_interview.isoformat(),
            iv.time_of_interview.strftime("%H:%M") if iv.time_of_interview else "",
            iv.student.username, iv.group.name, iv.company_name, iv.role,
            iv.get_interview_type_display(), iv.final_label, iv.get_attendance_display(),
            iv.progress, iv.hr_name, iv.hr_contact_number, iv.hr_email, iv.job_posting_url,
        ]])
    return response


def _apply_quick_action(iv, action, allow_override):
    """Returns an error message, or None when the change was applied."""
    if action == "offer":
        if iv.final_status != "in-progress" and not allow_override:
            return "This interview already has a final result."
        InterviewStatus.objects.update_or_create(
            interview=iv, defaults={"final_status": InterviewStatus.SELECTED})
        iv.rounds.update(status=InterviewRound.CLEARED)
        if not iv.attendance:
            iv.attendance = Interview.ATTENDED
            iv.save(update_fields=["attendance"])
        return None
    if action in dict(Interview.ATTENDANCE_CHOICES):
        iv.attendance = "" if iv.attendance == action else action
        iv.save(update_fields=["attendance"])
        return None
    return "Unknown action."


def _quick_redirect(request, fallback):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(fallback)


@student_required
@require_POST
def interview_quick(request, pk):
    iv = _own_interview(request, pk)
    error = _apply_quick_action(iv, request.POST.get("action", ""), allow_override=False)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Interview updated.")
    return _quick_redirect(request, reverse("student_interviews"))


@staff_required
@require_POST
def admin_interview_quick(request, pk):
    iv = get_object_or_404(Interview, pk=pk, group__admin=request.user)
    error = _apply_quick_action(iv, request.POST.get("action", ""), allow_override=True)
    if error:
        messages.error(request, error)
    else:
        messages.success(request, "Interview updated.")
    return _quick_redirect(request, reverse("admin_interviews"))


@admin_required
def admin_reports(request):
    interviews = (
        Interview.objects.filter(
            group__admin=request.user,
            status__final_status__in=(
                InterviewStatus.SELECTED,
                InterviewStatus.NOT_SELECTED,
            ),
        )
        .select_related("student", "group", "status")
        .order_by("status__final_status", "company_name", "student__username")
    )
    return render(request, "tracker/admin/reports.html", {
        "reports": [
            (
                InterviewStatus.SELECTED,
                "Selected",
                interviews.filter(status__final_status=InterviewStatus.SELECTED),
            ),
            (
                InterviewStatus.NOT_SELECTED,
                "Not selected",
                interviews.filter(status__final_status=InterviewStatus.NOT_SELECTED),
            ),
        ],
    })


@staff_required
def admin_interview_detail(request, pk):
    owner = request.user.data_owner
    interview = get_object_or_404(
        Interview.objects.select_related("student", "group", "status").prefetch_related("rounds"),
        pk=pk,
        group__admin=owner,
    )
    trainer_notes = None
    if request.user.is_admin:
        trainer_notes = list(interview.trainer_notes.select_related("author").prefetch_related("replies__author"))
        InterviewNoteReply.objects.filter(note__interview=interview, seen_by_admin=False).update(seen_by_admin=True)
    return render(request, "tracker/admin/interview_detail.html", {
        "iv": interview,
        "notes_form": InterviewAdminNotesForm() if request.user.is_admin else None,
        "trainer_notes": trainer_notes,
    })
