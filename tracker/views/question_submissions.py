import csv
import io

from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from ..models import LearningCourse, StudentQuestionSubmission
from ..permissions import staff_required, student_required

MAX_UPLOAD_BYTES = 1_000_000
MAX_UPLOAD_ROWS = 500


class CSVValidationError(ValueError):
    pass


def parse_question_csv(upload):
    if upload is None:
        raise CSVValidationError("Choose a CSV file to upload.")
    if upload.size > MAX_UPLOAD_BYTES:
        raise CSVValidationError("The CSV must be no larger than 1 MB.")
    try:
        text = upload.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CSVValidationError("The CSV must be UTF-8 encoded.") from exc
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        header = next(reader, None)
        if not header:
            raise CSVValidationError("The CSV must contain topic and question column headers.")
        columns = [column.strip().lower() for column in header]
        if len(columns) != len(set(columns)) or not {"topic", "question"} <= set(columns):
            raise CSVValidationError("The CSV must have unique topic and question column headers.")
        topic_index, question_index = columns.index("topic"), columns.index("question")
        questions = []
        seen = set()
        for row in reader:
            if not row or all(not value.strip() for value in row):
                continue
            if len(questions) >= MAX_UPLOAD_ROWS:
                raise CSVValidationError(f"The CSV can contain at most {MAX_UPLOAD_ROWS} questions.")
            if len(row) != len(columns):
                raise CSVValidationError(f"CSV line {reader.line_num} has the wrong number of columns.")
            topic, question = row[topic_index].strip(), row[question_index].strip()
            if (not topic or not question or len(topic) > 120 or len(question) > 2000
                    or "\x00" in topic or "\x00" in question):
                raise CSVValidationError(
                    f"CSV line {reader.line_num}: topic and question are required; "
                    "topic must be at most 120 characters, question at most 2000, "
                    "and neither may contain null characters."
                )
            key = (topic.casefold(), question.casefold())
            if key in seen:
                raise CSVValidationError(f"CSV line {reader.line_num} duplicates an earlier question.")
            seen.add(key)
            questions.append((topic, question))
        if not questions:
            raise CSVValidationError("The CSV contains no questions.")
        return questions
    except csv.Error as exc:
        raise CSVValidationError("The CSV could not be parsed. Check its formatting and field lengths.") from exc


@student_required
@require_GET
def student_question_uploads(request):
    submissions = request.user.question_submissions.all()
    return render(request, "tracker/student/question_uploads.html", {
        "page_obj": Paginator(submissions, 25).get_page(request.GET.get("page")),
        "max_rows": MAX_UPLOAD_ROWS,
    })


@student_required
@require_POST
def student_question_upload(request):
    try:
        questions = parse_question_csv(request.FILES.get("file"))
    except CSVValidationError as exc:
        messages.error(request, str(exc))
        return redirect("student_question_uploads")
    StudentQuestionSubmission.objects.bulk_create([
        StudentQuestionSubmission(student=request.user, topic=topic, question=question)
        for topic, question in questions
    ])
    messages.success(request, f"{len(questions)} question(s) submitted for staff review.")
    return redirect("student_question_uploads")


@staff_required
@require_GET
def admin_question_submissions(request):
    status = request.GET.get("status", StudentQuestionSubmission.PENDING)
    if status not in {StudentQuestionSubmission.PENDING, StudentQuestionSubmission.APPROVED,
                      StudentQuestionSubmission.REJECTED, "all"}:
        status = StudentQuestionSubmission.PENDING
    submissions = StudentQuestionSubmission.objects.select_related("student", "reviewed_by")
    if status != "all":
        submissions = submissions.filter(status=status)
    page_obj = Paginator(submissions, 25).get_page(request.GET.get("page"))
    courses = list(LearningCourse.objects.all())
    for submission in page_obj:
        submission.suggested_course_id = submission.course_id or next(
            (course.pk for course in courses if course.name.casefold() == submission.topic.casefold()), None
        )
    return render(request, "tracker/admin/question_submissions.html", {
        "page_obj": page_obj,
        "courses": courses,
        "status": status,
        "pending_count": StudentQuestionSubmission.objects.filter(status=StudentQuestionSubmission.PENDING).count(),
    })


@staff_required
@require_POST
def admin_question_submission_review(request, pk, decision):
    submission = get_object_or_404(StudentQuestionSubmission, pk=pk)
    if decision not in {StudentQuestionSubmission.APPROVED, StudentQuestionSubmission.REJECTED}:
        messages.error(request, "Choose approve or reject.")
        return redirect("admin_question_submissions")
    if decision == StudentQuestionSubmission.APPROVED:
        course_id = request.POST.get("course", "")
        course = LearningCourse.objects.filter(pk=int(course_id)).first() if course_id.isdigit() else None
        if course is None:
            messages.error(request, "Select a valid course before approving this question.")
            return redirect("admin_question_submissions")
        submission.course = course
    else:
        submission.course = None
    submission.status = decision
    submission.reviewed_by = request.user
    submission.reviewed_at = timezone.now()
    submission.save(update_fields=["status", "course", "reviewed_by", "reviewed_at"])
    messages.success(request, f"Question {submission.get_status_display().lower()}.")
    return redirect("admin_question_submissions")
