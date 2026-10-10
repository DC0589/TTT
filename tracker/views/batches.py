import logging

from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from ..forms import (
    AddMemberForm,
    GroupForm,
)
from ..models import (
    Group,
    GroupMembership,
    Interview,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required, staff_required


@staff_required
@require_GET
def admin_groups(request):
    view_mode = request.GET.get("view", "table")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "table"
    owner = request.user.data_owner
    groups = Group.objects.filter(admin=owner).annotate(
        member_count=Count("memberships", distinct=True),
        interview_count=Count("interviews", distinct=True),
    ).order_by("name")
    return render(request, "tracker/admin/groups.html", {
        "groups": groups,
        "view_mode": view_mode,
        "can_manage": request.user.is_admin,
        "can_add": True,
    })


@staff_required
def admin_group_add(request):
    form = GroupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        group = form.save(commit=False)
        group.admin = request.user.data_owner
        group.save()
        messages.success(request, "Batch created.")
        return redirect("admin_group_detail", pk=group.pk)
    return render(request, "tracker/admin/group_form.html", {
        "form": form,
        "page_title": "Create a batch",
        "submit_label": "Create batch",
    })


@admin_required
def admin_group_edit(request, pk):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
    form = GroupForm(request.POST or None, instance=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Batch details updated.")
        return redirect("admin_group_detail", pk=group.pk)
    return render(request, "tracker/admin/group_form.html", {
        "form": form,
        "group": group,
        "page_title": f"Edit {group.name}",
        "submit_label": "Save batch",
    })


@staff_required
@require_GET
def admin_group_detail(request, pk):
    owner = request.user.data_owner
    group = get_object_or_404(Group, pk=pk, admin=owner)
    return render(request, "tracker/admin/group_detail.html", {
        "group": group,
        "memberships": group.memberships.select_related("student"),
        "interviews": Interview.objects.filter(
            student__memberships__group=group,
            group__admin=owner,
        ).select_related("student", "group", "status").prefetch_related("rounds"),
        "can_manage": request.user.is_admin,
        "can_add": True,
    })


@staff_required
def admin_group_member_add(request, pk):
    owner = request.user.data_owner
    group = get_object_or_404(Group, pk=pk, admin=owner)
    form = AddMemberForm(request.POST or None, group=group)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Student added to batch.")
        return redirect("admin_group_detail", pk=pk)
    return render(request, "tracker/admin/member_form.html", {
        "group": group,
        "form": form,
    })


@admin_required
@require_POST
def group_delete(request, pk):
    get_object_or_404(Group, pk=pk, admin=request.user).delete()
    messages.success(request, "Batch deleted.")
    return redirect("admin_groups")


@admin_required
@require_POST
def member_remove(request, pk, student_id):
    group = get_object_or_404(Group, pk=pk, admin=request.user)
    membership = get_object_or_404(GroupMembership, group=group, student_id=student_id)
    membership.delete()
    messages.success(request, "Student removed from batch. Their interview records have been kept.")
    return redirect("admin_group_detail", pk=pk)
