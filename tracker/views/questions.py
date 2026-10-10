import csv
import io
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from ..forms import (
    MockQuestionForm,
)
from ..mock_topics import MOCK_TOPICS
from ..models import (
    MockQuestion,
)

logger = logging.getLogger(__name__)

from ..permissions import admin_required
from .mock import _csv_safe

QUESTION_CSV_COLUMNS = ["topic", "difficulty", "type", "text", "language"]


@admin_required
@require_GET
def admin_questions(request):
    questions = MockQuestion.objects.all()
    topic = request.GET.get("topic", "")
    difficulty = request.GET.get("difficulty", "")
    kind = request.GET.get("type", "")
    search = request.GET.get("q", "").strip()
    if topic:
        questions = questions.filter(topic=topic)
    if difficulty in {"easy", "medium", "hard"}:
        questions = questions.filter(difficulty=difficulty)
    if kind in {"concept", "coding"}:
        questions = questions.filter(kind=kind)
    if search:
        questions = questions.filter(text__icontains=search)
    page_obj = Paginator(questions, 25).get_page(request.GET.get("page"))
    query = request.GET.copy()
    query.pop("page", None)
    return render(request, "tracker/admin/questions.html", {
        "page_obj": page_obj,
        "topics": MockQuestion.objects.order_by().values_list("topic", flat=True).distinct().order_by("topic"),
        "selected_topic": topic, "selected_difficulty": difficulty,
        "selected_type": kind, "search": search,
        "total": MockQuestion.objects.count(),
        "base_query": query.urlencode(),
    })


def _question_form_page(request, form, title, label):
    return render(request, "tracker/admin/question_form.html", {
        "form": form, "page_title": title, "submit_label": label,
        "topic_suggestions": sorted(
            set(MOCK_TOPICS) | set(MockQuestion.objects.values_list("topic", flat=True))
        ),
    })


@admin_required
@require_http_methods(["GET", "POST"])
def admin_question_add(request):
    form = MockQuestionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question added to the bank.")
        return redirect("admin_questions")
    return _question_form_page(request, form, "Add a mock interview question", "Add question")


@admin_required
@require_http_methods(["GET", "POST"])
def admin_question_edit(request, pk):
    question = get_object_or_404(MockQuestion, pk=pk)
    form = MockQuestionForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Question updated.")
        return redirect("admin_questions")
    return _question_form_page(request, form, "Edit question", "Save changes")


@admin_required
@require_POST
def admin_question_delete(request, pk):
    get_object_or_404(MockQuestion, pk=pk).delete()
    messages.success(request, "Question deleted.")
    return redirect("admin_questions")


@admin_required
@require_GET
def admin_questions_export(request):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mock-question-bank.csv"'
    writer = csv.writer(response)
    writer.writerow(QUESTION_CSV_COLUMNS)
    for q in MockQuestion.objects.all():
        writer.writerow([_csv_safe(x) for x in [q.topic, q.difficulty, q.kind, q.text, q.language]])
    return response


@admin_required
@require_POST
def admin_questions_import(request):
    upload = request.FILES.get("file")
    if upload is None or upload.size > 1_000_000:
        messages.error(request, "Choose a CSV file smaller than 1 MB.")
        return redirect("admin_questions")
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        messages.error(request, "The file must be UTF-8 encoded CSV.")
        return redirect("admin_questions")
    reader = csv.DictReader(io.StringIO(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if not {"topic", "difficulty", "text"} <= headers:
        messages.error(request, "The CSV needs the columns: topic, difficulty, type, text, language.")
        return redirect("admin_questions")
    existing = {
        (t.lower(), d, x.strip().lower())
        for t, d, x in MockQuestion.objects.values_list("topic", "difficulty", "text")
    }
    created, skipped = [], 0
    for index, raw in enumerate(reader):
        if index >= 2000:
            break
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        topic = " ".join(row.get("topic", "").split())[:120]
        difficulty = row.get("difficulty", "").lower() or "medium"
        kind = (row.get("type") or row.get("kind") or "concept").lower()
        language = row.get("language", "").lower()
        body = row.get("text", "")
        if kind == "code":
            kind = "coding"
        if (not topic or not body or difficulty not in {"easy", "medium", "hard"}
                or kind not in {"concept", "coding"} or language not in {"", "python", "sql", "pyspark"}):
            skipped += 1
            continue
        key = (topic.lower(), difficulty, body.lower())
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        created.append(MockQuestion(
            topic=topic, difficulty=difficulty, kind=kind, text=body,
            language=language if kind == "coding" else "",
        ))
    MockQuestion.objects.bulk_create(created)
    messages.success(request, f"Imported {len(created)} question(s); skipped {skipped} duplicate or invalid row(s).")
    return redirect("admin_questions")
