import logging

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from ..forms import (
    InterviewAdminNotesForm,
    InterviewNotesForm,
    NoteReplyForm,
)
from ..models import (
    Interview,
    InterviewNote,
    InterviewNoteReply,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required, staff_required, student_required
from .interviews import _own_interview


@staff_required
@require_POST
def admin_interview_notes(request, pk):
    iv = get_object_or_404(Interview, pk=pk, group__admin=request.user)
    form = InterviewAdminNotesForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.interview = iv
        note.author = request.user
        note.save()
        messages.success(request, "Trainer note saved.")
    else:
        messages.error(request, "Write a note before saving.")
    return redirect("admin_interview_detail", pk=pk)


@student_required
@require_GET
def student_notes(request):
    notes = (InterviewNote.objects
             .filter(interview__student=request.user, visible_to_student=True)
             .select_related("interview", "author").prefetch_related("replies__author")
             .order_by("interview__company_name", "-created_at"))
    companies = {}
    for note in notes:
        companies.setdefault(note.interview, []).append(note)
    return render(request, "tracker/student/notes.html", {
        "companies": companies.items(), "total": len(notes),
    })


@admin_required
@require_POST
def admin_note_reply(request, pk):
    note = get_object_or_404(InterviewNote, pk=pk, interview__group__admin=request.user)
    form = NoteReplyForm(request.POST)
    if not note.visible_to_student:
        messages.error(request, "Only notes shown to the student can be replied to.")
    elif form.is_valid():
        InterviewNoteReply.objects.create(
            note=note, author=request.user, text=form.cleaned_data["text"], seen_by_admin=True)
        messages.success(request, "Reply sent.")
    else:
        messages.error(request, "Write a reply before sending.")
    return redirect("admin_interview_detail", pk=note.interview_id)


@student_required
@require_POST
def student_note_reply(request, pk):
    note = get_object_or_404(InterviewNote, pk=pk, interview__student=request.user, visible_to_student=True)
    form = NoteReplyForm(request.POST)
    if form.is_valid():
        InterviewNoteReply.objects.create(note=note, author=request.user, text=form.cleaned_data["text"])
        messages.success(request, "Reply sent to your trainer.")
    else:
        messages.error(request, "Write a reply before sending.")
    return redirect("student_notes")


@student_required
@require_POST
def interview_notes(request, pk):
    iv = _own_interview(request, pk)
    form = InterviewNotesForm(request.POST, instance=iv)
    if form.is_valid():
        form.save()
        messages.success(request, "Preparation notes saved.")
    return redirect("student_interview_detail", pk=iv.pk)
