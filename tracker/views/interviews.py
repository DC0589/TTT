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
        iv = form.save(commit=False)
        iv.student = request.user
        iv.save()
        round_type = form.cleaned_data["first_round_type"]
        InterviewRound.objects.create(
            interview=iv, round_number=1, round_type=round_type,
            description=dict(InterviewRound.TYPE_CHOICES)[round_type],
            scheduled_date=iv.date_of_interview, scheduled_time=iv.time_of_interview)
        messages.success(request, "Interview added. After it happens, update the round result and add your feedback.")
        return redirect("student_interview_detail", pk=iv.pk)
    return render(request, "tracker/student/interview_form.html", {
        "form": form,
        "page_title": "Add an interview",
        "submit_label": "Save interview",
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
        rounds = iv.rounds.order_by("round_number")
        if obj.final_status == InterviewStatus.SELECTED:
            rounds.update(status=InterviewRound.CLEARED)
        else:
            last_round = rounds.last()
            if last_round:
                last_round.status = InterviewRound.REJECTED
                last_round.save(update_fields=["status"])
        messages.success(request, "Final result saved.")
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
    if rnd.interview.rounds.filter(status=InterviewRound.PENDING).exists():
        InterviewStatus.objects.filter(interview=rnd.interview).delete()
    return JsonResponse({
        "status": rnd.status,
        "feedback": rnd.feedback,
        "badge": rnd.badge,
        "final_status": rnd.interview.final_status,
        "final_label": rnd.interview.final_label,
    })
