import logging

from django.db.models import Avg, Count, Q
from django.shortcuts import render
from django.utils import timezone

from ..models import (
    Interview,
    InterviewNoteReply,
    StudentRegistrationRequest,
    User,
)

logger = logging.getLogger(__name__)

from ..permissions import staff_required, student_required
from .calendar import _reminders


@staff_required
def admin_dashboard(request):
    interviews = (Interview.objects.filter(group__admin=request.user)
                  .select_related("student", "group", "status").prefetch_related("rounds"))
    students = User.objects.filter(is_student=True).filter(
        Q(memberships__group__admin=request.user)
        | Q(created_by=request.user)
        | Q(created_by__created_by=request.user)
    ).distinct()
    stats = {
        "students": students.count(),
        "groups": request.user.groups_created.count(),
        "interviews": interviews.count(),
        "selected": interviews.filter(status__final_status="selected").count(),
    }
    outcome_counts = {
        "in_progress": interviews.filter(status__isnull=True).count(),
        "selected": stats["selected"],
        "not_selected": interviews.filter(status__final_status="not-selected").count(),
    }
    decided = outcome_counts["selected"] + outcome_counts["not_selected"]
    outcome_chart = [
        {
            "label": label,
            "count": count,
            "percent": round(count * 100 / max(interviews.count(), 1)),
        }
        for label, count in (
            ("In progress", outcome_counts["in_progress"]),
            ("Selected", outcome_counts["selected"]),
            ("Not selected", outcome_counts["not_selected"]),
        )
    ]
    batches = list(request.user.groups_created.order_by("name"))
    batch_id = request.GET.get("batch", "")
    selected_batch = next((b for b in batches if str(b.pk) == batch_id), None)
    roster = students
    if selected_batch:
        roster = roster.filter(memberships__group=selected_batch)
    roster = roster.annotate(
        mock_count=Count("mock_interview_sessions", filter=Q(mock_interview_sessions__rating__isnull=False), distinct=True),
        mock_avg=Avg("mock_interview_sessions__rating"),
        interview_count=Count("interviews", filter=Q(interviews__group__admin=request.user), distinct=True),
        selected_count=Count("interviews", filter=Q(
            interviews__group__admin=request.user, interviews__status__final_status="selected"), distinct=True),
    ).order_by("username")
    progress_rows = []
    for student in roster[:100]:
        avg = float(student.mock_avg) if student.mock_avg is not None else None
        progress_rows.append({
            "student": student, "mock_count": student.mock_count,
            "mock_avg": round(avg, 2) if avg is not None else None,
            "percent": round(avg / 5 * 100) if avg is not None else 0,
            "weak": avg is not None and avg < 3,
        })
    unseen_replies = list(
        InterviewNoteReply.objects.filter(
            seen_by_admin=False, note__interview__group__admin=request.user)
        .select_related("note__interview__student").order_by("-created_at")[:10])
    return render(request, "tracker/admin/dashboard.html", {
        "unseen_replies": unseen_replies,
        "unseen_reply_count": InterviewNoteReply.objects.filter(
            seen_by_admin=False, note__interview__group__admin=request.user).count(),
        "stats": stats, "interviews": interviews[:20],
        "progress_rows": progress_rows, "batches": batches, "selected_batch": selected_batch,
        "roster_total": roster.count(),
        "outcome_chart": outcome_chart,
        "selection_rate": round(outcome_counts["selected"] * 100 / decided) if decided else 0,
        "pending_registrations": StudentRegistrationRequest.objects.filter(
            status=StudentRegistrationRequest.AWAITING_APPROVAL
        ).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })


@student_required
def student_dashboard(request):
    interviews = request.user.interviews.select_related("group", "status").prefetch_related("rounds")
    return render(request, "tracker/student/dashboard.html", {
        "groups": request.user.student_groups.all(), "interviews": interviews[:5],
        "active_tab": "overview",
        "reminders": _reminders(request.user),
        "total": interviews.count(),
        "selected": interviews.filter(status__final_status="selected").count(),
        "in_progress": interviews.filter(status__isnull=True).count(),
        "upcoming_interviews": interviews.filter(
            date_of_interview__gte=timezone.localdate()
        ).order_by("date_of_interview", "company_name")[:5],
    })
