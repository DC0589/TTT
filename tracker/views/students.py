import logging
import secrets

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from ..forms import (
    HRUserForm,
    StudentForm,
)
from ..models import (
    GroupMembership,
    Interview,
    InterviewStatus,
    User,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required, hr_required, staff_required
from .mock import _mock_progress


@admin_required
def admin_hr_user_add(request):
    form = HRUserForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        hr_user = form.save(commit=False)
        hr_user.created_by = request.user
        hr_user.save()
        messages.success(request, f"HR account {hr_user.username} created.")
        return redirect("admin_hr_user_add")
    return render(request, "tracker/admin/hr_user_form.html", {"form": form})


@hr_required
def hr_students(request):
    search = request.GET.get("q", "").strip()[:100]
    students = User.objects.filter(is_student=True, created_by=request.user)
    if request.user.created_by_id:
        students = User.objects.filter(is_student=True).filter(
            Q(created_by=request.user)
            | Q(memberships__group__admin=request.user.created_by)
        ).distinct()
    if search:
        students = students.filter(
            Q(username__icontains=search) | Q(email__icontains=search)
        )
    page_obj = Paginator(students.order_by("username"), 25).get_page(
        request.GET.get("page")
    )
    return render(request, "tracker/hr/students.html", {
        "students": page_obj.object_list,
        "page_obj": page_obj,
        "search": search,
    })


@staff_required
def admin_student_add(request):
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        student = form.save(commit=False)
        student.created_by = request.user
        student.save()
        messages.success(request, "Student added.")
        if request.user.is_hr:
            return redirect("hr_students")
        return redirect("admin_student_detail", pk=student.pk)
    return render(request, "tracker/admin/student_form.html", {
        "form": form,
        "page_title": "Add a student",
    })


@admin_required
@require_POST
def student_delete(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct(),
        pk=pk,
    )
    student.delete()
    messages.success(request, "Student removed.")
    return redirect("admin_dashboard")


@admin_required
def admin_students(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    search = request.GET.get("q", "").strip()[:100]
    students = User.objects.filter(is_student=True).filter(
        Q(memberships__group__admin=request.user)
        | Q(created_by=request.user)
        | Q(created_by__created_by=request.user)
    ).annotate(
        interview_count=Count(
            "interviews", filter=Q(interviews__group__admin=request.user), distinct=True
        ),
        selected_count=Count(
            "interviews",
            filter=Q(
                interviews__group__admin=request.user,
                interviews__status__final_status="selected",
            ),
            distinct=True,
        ),
        upcoming_count=Count(
            "interviews",
            filter=Q(
                interviews__group__admin=request.user,
                interviews__date_of_interview__gte=timezone.localdate(),
            ),
            distinct=True,
        ),
    ).distinct().prefetch_related(Prefetch(
        "memberships",
        queryset=GroupMembership.objects.filter(
            group__admin=request.user
        ).select_related("group"),
        to_attr="admin_memberships",
    )).order_by("username")
    if search:
        students = students.filter(
            Q(username__icontains=search)
            | Q(email__icontains=search)
            | Q(memberships__group__name__icontains=search, memberships__group__admin=request.user)
        ).distinct()
    page_obj = Paginator(students, 25).get_page(request.GET.get("page"))
    return render(request, "tracker/admin/students.html", {
        "students": page_obj.object_list,
        "page_obj": page_obj,
        "view_mode": view_mode,
        "search": search,
        "total_students": page_obj.paginator.count,
    })


@admin_required
@require_POST
def admin_student_reset_password(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct(),
        pk=pk,
    )
    temporary_password = secrets.token_urlsafe(9)
    student.set_password(temporary_password)
    student.save(update_fields=["password"])
    messages.success(
        request,
        f"Temporary password for {student.username}: {temporary_password} "
        "- share it securely; it will not be shown again.",
    )
    return redirect("admin_student_detail", pk=student.pk)


@admin_required
def admin_student_detail(request, pk):
    student = get_object_or_404(
        User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=request.user)
            | Q(created_by=request.user)
            | Q(created_by__created_by=request.user)
        ).distinct().prefetch_related("memberships__group"),
        pk=pk,
    )
    interviews = (Interview.objects.filter(
        student=student, group__admin=request.user
    ).select_related("group", "status").prefetch_related("rounds"))
    placements = [
        {"company": i.company_name, "role": i.role, "date": i.status.updated_at, "source": "Interview"}
        for i in interviews if i.final_status == InterviewStatus.SELECTED
    ] + [
        {"company": x.company, "role": x.role, "date": x.created_at, "source": "Added by staff"}
        for x in student.selections.all()
    ]
    placements.sort(key=lambda p: p["date"], reverse=True)
    from ..services import mock_scoring
    mock_scoring.process_pending(max_items=1, time_budget=10)
    return render(request, "tracker/admin/student_detail.html", {
        "student": student,
        "placements": placements,
        "memberships": student.memberships.filter(
            group__admin=request.user).select_related("group"),
        "interviews": interviews,
        "progress": _mock_progress(student),
        "mock_sessions": student.mock_interview_sessions.order_by("-created_at")[:15],
    })
