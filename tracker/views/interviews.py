import logging

from django.contrib import messages
from django.db import transaction
from django.db.models import Max
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET, require_POST

from ..forms import (
    FinalStatusForm,
    InterviewForm,
    RoundCountForm,
    RoundDetailsForm,
    RoundForm,
    RoundScheduleForm,
    RoundStatusForm,
)
from ..models import (
    Interview,
    InterviewRound,
    InterviewStatus,
)

logger = logging.getLogger(__name__)

from ..permissions import student_required


def _own_interview(request, pk):
    return get_object_or_404(Interview.objects.select_related("group"), pk=pk, student=request.user)


@student_required
@require_POST
def round_schedule(request, pk):
    rnd = get_object_or_404(InterviewRound, pk=pk, interview__student=request.user)
    form = RoundScheduleForm(request.POST, instance=rnd)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    form.save()
    return JsonResponse({"scheduled_date": rnd.scheduled_date.isoformat() if rnd.scheduled_date else ""})


@student_required
@require_GET
def student_interviews(request):
    view_mode = request.GET.get("view", "cards")
    if view_mode not in {"cards", "table", "list"}:
        view_mode = "cards"
    interviews = request.user.interviews.select_related(
        "group", "status"
    ).prefetch_related("rounds")
    return render(request, "tracker/student/interviews.html", {
        "interviews": interviews,
        "view_mode": view_mode,
    })


@student_required
def student_interview_add(request):
    form = InterviewForm(request.POST or None, student=request.user)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            iv = form.save(commit=False)
            iv.student = request.user
            iv.save()
            round_type = form.cleaned_data["first_round_type"]
            description = form.cleaned_data.get("first_round_description") or dict(
                InterviewRound.TYPE_CHOICES
            )[round_type]
            count = form.cleaned_data.get("round_count") or 1
            InterviewRound.objects.create(
                interview=iv, round_number=1, round_type=round_type,
                description=description,
                scheduled_date=iv.date_of_interview, scheduled_time=iv.time_of_interview)
            additional = []
            for number in range(2, count + 1):
                kind = form.cleaned_data.get(f"round_{number}_type") or ""
                name = (form.cleaned_data.get(f"round_{number}_description") or "").strip()
                name = name or dict(InterviewRound.TYPE_CHOICES).get(
                    kind, f"Round {number} (details not set)"
                )
                additional.append(InterviewRound(
                    interview=iv, round_number=number, round_type=kind, description=name
                ))
            InterviewRound.objects.bulk_create(additional)
        messages.success(request, "Interview added. You can update each planned round as details become available.")
        return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_form.html", {
        "form": form,
        "page_title": "Add an interview",
        "submit_label": "Create interview",
    })


@student_required
def student_interview_detail(request, pk):
    iv = _own_interview(request, pk)
    return render(request, "tracker/student/interview_detail.html", {
        "iv": iv, "round_form": RoundForm(),
        "status_form": FinalStatusForm(instance=getattr(iv, "status", None), interview=iv),
        "round_choices": InterviewRound.STATUS_CHOICES,
        "round_type_choices": InterviewRound.TYPE_CHOICES,
        "editable": False,
        "edit_mode": False,
    })


@student_required
def student_interview_edit(request, pk):
    iv = _own_interview(request, pk)
    form = InterviewForm(
        request.POST or None,
        instance=iv,
        student=request.user,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Interview details updated.")
        return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_form.html", {
        "iv": iv,
        "form": form,
        "page_title": "Edit interview details",
        "submit_label": "Save interview details",
    })


@student_required
def student_interview_progress(request, pk):
    iv = _own_interview(request, pk)
    return render(request, "tracker/student/interview_detail.html", {
        "iv": iv,
        "round_form": RoundForm(),
        "status_form": FinalStatusForm(instance=getattr(iv, "status", None), interview=iv),
        "round_choices": InterviewRound.STATUS_CHOICES,
        "round_type_choices": InterviewRound.TYPE_CHOICES,
        "editable": True,
        "edit_mode": True,
    })


@student_required
def interview_round_plan(request, pk):
    iv = _own_interview(request, pk)
    form = RoundCountForm(request.POST or None, initial={"round_count": iv.rounds.count() or 1})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            iv = get_object_or_404(
                Interview.objects.select_for_update(), pk=pk, student=request.user
            )
            rounds = list(iv.rounds.select_for_update().order_by("round_number"))
            count = form.cleaned_data["round_count"]
            removed = rounds[count:]
            if any(r.status != InterviewRound.PENDING or r.round_type or r.feedback
                   or r.scheduled_date or r.scheduled_time
                   or r.description != f"Round {r.round_number} (details not set)" for r in removed):
                form.add_error("round_count", "Only empty pending rounds at the end can be removed.")
            else:
                InterviewRound.objects.filter(pk__in=[r.pk for r in removed]).delete()
                next_number = rounds[-1].round_number + 1 if rounds and not removed else (
                    rounds[count - 1].round_number + 1 if count <= len(rounds) else 1
                )
                InterviewRound.objects.bulk_create([
                    InterviewRound(interview=iv, round_number=number,
                                   description=f"Round {number} (details not set)")
                    for number in range(next_number, next_number + max(0, count - len(rounds)))
                ])
                if count > len(rounds) and iv.final_status == "selected":
                    InterviewStatus.objects.filter(interview=iv).delete()
                messages.success(request, "Interview plan updated.")
                return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_round_plan.html", {"iv": iv, "form": form})


@student_required
def interview_round_edit(request, pk):
    rnd = get_object_or_404(InterviewRound.objects.select_related("interview"),
                            pk=pk, interview__student=request.user)
    if request.method == "POST":
        with transaction.atomic():
            iv = get_object_or_404(
                Interview.objects.select_for_update(), pk=rnd.interview_id, student=request.user
            )
            rnd = get_object_or_404(InterviewRound.objects.select_for_update(), pk=pk, interview=iv)
            form = RoundDetailsForm(request.POST, instance=rnd)
            if form.is_valid():
                form.save()
                if rnd.status != InterviewRound.CLEARED:
                    InterviewStatus.objects.filter(
                        interview=iv, final_status=InterviewStatus.SELECTED
                    ).delete()
                messages.success(request, "Round updated.")
                if request.headers.get("x-requested-with") == "XMLHttpRequest":
                    return JsonResponse({
                        "round_number": rnd.round_number,
                        "description": rnd.description,
                        "status": rnd.status,
                    })
                return redirect("student_interview_detail", pk=iv.pk)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    else:
        form = RoundDetailsForm(instance=rnd)
    return render(request, "tracker/student/interview_round_edit.html", {"round": rnd, "form": form})


@student_required
@require_POST
def interview_status(request, pk):
    iv = _own_interview(request, pk)
    if iv.final_status != "in-progress" and request.POST.get("edit_mode") != "1":
        messages.error(request, "Open this interview in edit mode to change its final result.")
        return redirect("student_interviews")
    form = FinalStatusForm(request.POST, instance=getattr(iv, "status", None), interview=iv)
    if form.is_valid():
        obj = form.save(commit=False)
        obj.interview = iv
        obj.save()
        messages.success(request, "Final result saved.")
        if request.POST.get("edit_mode") == "1":
            return redirect("student_interview_detail", pk=iv.pk)
        return redirect("student_interviews")
    else:
        for err in form.errors.values():
            messages.error(request, " ".join(err))
    return redirect("student_interview_detail", pk=pk)


@student_required
@require_POST
def interview_delete(request, pk):
    _own_interview(request, pk).delete()
    messages.success(request, "Interview deleted.")
    return redirect("student_interviews")


@student_required
@require_POST
def round_add(request, pk):
    """AJAX: CSRF-protected via the X-CSRFToken header."""
    iv = _own_interview(request, pk)
    if iv.final_status != "in-progress" and request.POST.get("edit_mode") != "1":
        return JsonResponse({"error": "Open this interview in edit mode to change its rounds."}, status=409)
    if iv.rounds.filter(status=InterviewRound.PENDING).exists():
        return JsonResponse({"error": "Update the result of your current round before adding the next one."}, status=400)
    if iv.rounds.filter(status=InterviewRound.REJECTED).exists():
        return JsonResponse({"error": "You were rejected in an earlier round."}, status=400)
    form = RoundForm(request.POST, interview=iv)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    with transaction.atomic():
        nxt = (iv.rounds.aggregate(m=Max("round_number"))["m"] or 0) + 1
        rnd = InterviewRound.objects.create(
            interview=iv, round_number=nxt, description=form.cleaned_data["description"],
            round_type=form.cleaned_data["round_type"],
            scheduled_date=form.cleaned_data.get("scheduled_date") or (iv.date_of_interview if nxt == 1 else None),
            scheduled_time=form.cleaned_data.get("scheduled_time") or (iv.time_of_interview if nxt == 1 else None))
        InterviewStatus.objects.filter(interview=iv).delete()  # new round reopens the interview
    html = render_to_string("tracker/student/_round_row.html", {
        "r": rnd,
        "round_choices": InterviewRound.STATUS_CHOICES,
        "editable": True,
    }, request=request)
    return JsonResponse({"html": html}, status=201)


@student_required
@require_POST
def round_update(request, pk):
    rnd = get_object_or_404(InterviewRound, pk=pk, interview__student=request.user)
    form = RoundStatusForm(request.POST, instance=rnd)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors.get_json_data()}, status=400)
    form.save()
    if rnd.interview.rounds.exclude(status=InterviewRound.CLEARED).exists():
        InterviewStatus.objects.filter(
            interview=rnd.interview, final_status=InterviewStatus.SELECTED
        ).delete()
    return JsonResponse({
        "status": rnd.status,
        "feedback": rnd.feedback,
        "badge": rnd.badge,
        "final_status": rnd.interview.final_status,
        "final_label": rnd.interview.final_label,
    })
