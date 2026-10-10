import base64
import csv
import json
import logging
import re

from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_GET, require_POST

from ..ai_interview import GeminiAPIError, check_health
from ..mock_bank import normalise_difficulty
from ..mock_topics import MOCK_TOPICS
from ..models import (
    MockInterviewScore,
    MockInterviewSession,
    MockQuestion,
    User,
)

logger = logging.getLogger(__name__)

from ..permissions import staff_required, student_required
from ..services import mock_scoring, question_sets

MOCK_QUESTION_COUNT = 10
DRAIN_SECONDS = 25


MOCK_CODING_QUESTION_COUNT = 3


def _mock_review_filters(request):
    owner = request.user.data_owner
    groups = owner.groups_created.order_by("name")
    batch_value = request.GET.get("batch", "")
    selected_group = groups.filter(pk=int(batch_value)).first() if batch_value.isdigit() else None

    students = User.objects.filter(is_student=True).filter(
        Q(created_by=owner)
        | Q(created_by__created_by=owner)
        | Q(memberships__group__admin=owner)
    )
    if selected_group:
        students = students.filter(memberships__group=selected_group)
    students = students.distinct().order_by("username")
    student_value = request.GET.get("student", "")
    selected_student = students.filter(pk=int(student_value)).first() if student_value.isdigit() else None

    sessions = MockInterviewSession.objects.filter(
        student__is_student=True,
    ).filter(
        Q(student__created_by=owner)
        | Q(student__created_by__created_by=owner)
        | Q(student__memberships__group__admin=owner)
    ).select_related("student").prefetch_related("scores").distinct()
    if selected_group:
        sessions = sessions.filter(student__memberships__group=selected_group)
    if selected_student:
        sessions = sessions.filter(student=selected_student)

    topics = list(sessions.order_by().values_list("role", flat=True).distinct().order_by("role"))
    selected_topic = request.GET.get("topic", "")
    if selected_topic in topics:
        sessions = sessions.filter(role=selected_topic)
    else:
        selected_topic = ""

    from_date_value = request.GET.get("from_date", "")
    to_date_value = request.GET.get("to_date", "")
    from_date = parse_date(from_date_value)
    to_date = parse_date(to_date_value)
    if from_date:
        sessions = sessions.filter(created_at__date__gte=from_date)
    if to_date:
        sessions = sessions.filter(created_at__date__lte=to_date)

    return {
        "owner": owner, "groups": groups, "students": students, "sessions": sessions,
        "topics": topics, "selected_group": selected_group, "selected_student": selected_student,
        "selected_topic": selected_topic, "from_date_value": from_date_value, "to_date_value": to_date_value,
    }


@staff_required
@require_GET
def admin_mock_interviews(request):
    mock_scoring.process_pending(max_items=1, time_budget=10)
    f = _mock_review_filters(request)
    groups, students, sessions, topics = f["groups"], f["students"], f["sessions"], f["topics"]
    selected_group, selected_student, selected_topic = f["selected_group"], f["selected_student"], f["selected_topic"]
    from_date_value, to_date_value = f["from_date_value"], f["to_date_value"]
    paginator = Paginator(sessions.order_by("-created_at"), 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    def page_url(page_number):
        query = request.GET.copy()
        query["page"] = page_number
        return f"?{query.urlencode()}"

    return render(request, "tracker/admin/mock_interviews.html", {
        "batches": groups,
        "students": students,
        "topics": topics,
        "selected_batch": selected_group,
        "selected_student": selected_student,
        "selected_topic": selected_topic,
        "from_date": from_date_value,
        "to_date": to_date_value,
        "page_obj": page_obj,
        "previous_page_url": page_url(page_obj.previous_page_number()) if page_obj.has_previous() else None,
        "next_page_url": page_url(page_obj.next_page_number()) if page_obj.has_next() else None,
        "result_count": paginator.count,
        "export_query": request.GET.urlencode(),
    })


def _csv_safe(value):
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@staff_required
@require_GET
def admin_mock_interviews_export(request):
    f = _mock_review_filters(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="mock-interview-report.csv"'
    writer = csv.writer(response)
    writer.writerow([
        "Date (IST)", "Student", "Topic", "Level", "Rating (out of 5)", "Answers submitted",
        "Answers expected",
    ])
    for session in f["sessions"].order_by("-created_at"):
        writer.writerow([_csv_safe(x) for x in [
            timezone.localtime(session.created_at).strftime("%Y-%m-%d %H:%M"),
            session.student.username, session.role, session.difficulty or "",
            f"{session.rating:.1f}" if session.rating is not None else "",
            len(session.scores.all()), session.expected_answers,
        ]])
    return response


@staff_required
@require_GET
def admin_mock_interviews_report(request):
    f = _mock_review_filters(request)
    sessions = list(f["sessions"].order_by("student__username", "-created_at")[:300])
    return render(request, "tracker/admin/mock_report.html", {
        "sessions": sessions,
        "student": f["selected_student"],
        "batch": f["selected_group"],
        "topic": f["selected_topic"],
        "from_date": f["from_date_value"],
        "to_date": f["to_date_value"],
        "generated_at": timezone.localtime(),
    })


def _mock_progress(user):
    rated = list(user.mock_interview_sessions.filter(rating__isnull=False).order_by("created_at"))
    topics = {}
    for session in rated:
        topics.setdefault(session.role, []).append(float(session.rating))
    rows = []
    for topic, ratings in topics.items():
        average = sum(ratings) / len(ratings)
        rows.append({
            "topic": topic, "count": len(ratings), "average": round(average, 2),
            "percent": round(average / 5 * 100), "latest": ratings[-1],
            "weak": average < 3,
        })
    rows.sort(key=lambda row: row["average"])
    levels = []
    for level in ("easy", "medium", "hard"):
        values = [float(x.rating) for x in rated if x.difficulty == level]
        if values:
            average = sum(values) / len(values)
            levels.append({"level": level.title(), "average": round(average, 2),
                           "percent": round(average / 5 * 100), "count": len(values)})
    trend = [{"label": x.created_at.strftime("%b %d"), "topic": x.role,
              "rating": float(x.rating), "percent": round(float(x.rating) / 5 * 100)}
             for x in rated[-12:]]
    attempted = {row["topic"].lower() for row in rows}
    suggestion = None
    if rows and rows[0]["weak"]:
        suggestion = {"topic": rows[0]["topic"], "reason": "your lowest average so far"}
    else:
        for topic in MOCK_TOPICS:
            if topic.lower() not in attempted:
                suggestion = {"topic": topic, "reason": "you haven't tried it yet"}
                break
    overall = None
    if rated:
        values = [float(x.rating) for x in rated]
        overall = {
            "sessions": len(values), "average": round(sum(values) / len(values), 2),
            "best": max(values), "first": values[0], "latest": values[-1],
            "change": round(values[-1] - values[0], 2),
        }
    return {"topics": rows, "levels": levels, "trend": trend, "suggestion": suggestion, "overall": overall}


@student_required
@require_GET
def student_mock_interview(request):
    mock_scoring.process_pending(max_items=1, time_budget=15)
    return render(request, "tracker/student/mock_interview.html", {
        "progress": _mock_progress(request.user),
        "ai_url": reverse("student_mock_interview_ai"),
        "health_url": reverse("student_mock_interview_health"),
        "topics": list(MOCK_TOPICS) + sorted(
            set(MockQuestion.objects.filter(is_active=True).values_list("topic", flat=True)) - set(MOCK_TOPICS)
        ),
        "active_tab": "mock_interview",
        "recent_sessions": request.user.mock_interview_sessions.prefetch_related(
            "scores"
        )[:8],
    })


@student_required
def student_mock_interview_health(request):
    result = check_health(force=request.GET.get("refresh") == "1")
    public = {"status": result["status"], "message": result["message"]}
    return JsonResponse(public, status=200 if result["status"] != "down" else 503)


@student_required
@require_POST
def student_mock_interview_ai(request):
    if len(request.body) > 2_000_000:
        return JsonResponse({"error": "The request is too large. Try again."}, status=413)
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    if not isinstance(data, dict) or data.get("consent") is not True:
        return JsonResponse({"error": "Consent is required before AI analysis."}, status=400)

    action = data.get("action")
    session_id = data.get("session_id")
    if action == "drain":
        scored = mock_scoring.process_pending(max_items=1, time_budget=DRAIN_SECONDS)
        return JsonResponse({"scored": scored, "waiting": mock_scoring.pending_count()})
    if action in {"finish", "results", "retry"}:
        session = MockInterviewSession.objects.filter(
            pk=session_id, student=request.user
        ).first()
        if session is None:
            return JsonResponse({"error": "Interview session not found."}, status=404)
        if action == "retry":
            return JsonResponse({"requeued": mock_scoring.retry_failed(session)})

        if action == "finish":
            expected_answers = data.get("expected_answers", 0)
            if type(expected_answers) is not int or not 0 <= expected_answers <= MOCK_QUESTION_COUNT:
                return JsonResponse({"error": "Invalid answer count."}, status=400)
            session.expected_answers = max(session.expected_answers, expected_answers)
            end_reason = data.get("end_reason", "")
            end_reason = " ".join(end_reason.split())[:300] if isinstance(end_reason, str) else ""
            if session.expected_answers < MOCK_QUESTION_COUNT and not session.end_reason:
                if len(end_reason) < 5:
                    return JsonResponse(
                        {"error": "Tell us why you are ending before all 10 questions."}, status=400
                    )
                session.end_reason = end_reason
            if session.completed_at is None:
                session.completed_at = timezone.now()
            session.save(update_fields=["expected_answers", "completed_at", "end_reason"])

        failed_numbers = data.get("failed_question_numbers", [])
        if action == "results" and isinstance(failed_numbers, list):
            for failed_number in failed_numbers:
                if type(failed_number) is not int or not 1 <= failed_number <= MOCK_QUESTION_COUNT:
                    continue
                score, created = MockInterviewScore.objects.get_or_create(
                    session=session,
                    question_number=failed_number,
                    defaults={
                        "status": MockInterviewScore.FAILED,
                        "answer_feedback": "Feedback could not be loaded.",
                    },
                )
                if not created and score.status == MockInterviewScore.PENDING and score.payload is None:
                    score.status = MockInterviewScore.FAILED
                    score.answer_feedback = "Feedback could not be loaded."
                    score.save(update_fields=["status", "answer_feedback"])

        if action == "finish":
            return JsonResponse({"session_id": session.pk})

        if action == "results":
            mock_scoring.process_pending(max_items=1, session=session, time_budget=DRAIN_SECONDS)
            session.refresh_from_db()
        scores = list(session.scores.values(
            "question_number", "question", "status", "score",
            "answer_feedback", "camera_feedback", "screen_feedback",
        ))
        finished_answers = sum(
            score["status"] in {MockInterviewScore.COMPLETE, MockInterviewScore.FAILED}
            for score in scores
        )
        failed_answers = sum(score["status"] == MockInterviewScore.FAILED for score in scores)
        pending_count = max(0, session.expected_answers - finished_answers)
        retryable = session.scores.filter(
            status=MockInterviewScore.FAILED, payload__isnull=False
        ).count()
        return JsonResponse({
            "session_id": session.pk,
            "expected_answers": session.expected_answers,
            "finished_answers": finished_answers,
            "failed_answers": failed_answers,
            "retryable": retryable,
            "end_reason": session.end_reason,
            "pending_count": pending_count,
            "ready": pending_count == 0,
            "session_rating": float(session.rating) if session.rating is not None else None,
            "scores": scores,
        })

    role = data.get("role", "")
    if action not in {"question", "feedback"} or not isinstance(role, str):
        return JsonResponse({"error": "Invalid interview request."}, status=400)
    role = role.strip()[:120]
    if not role:
        return JsonResponse({"error": "Enter a role for the interview."}, status=400)

    session = None
    if action == "question":
        previous_questions = list(
            MockInterviewScore.objects.filter(
                session__student=request.user, session__role=role,
            ).exclude(question="").order_by("-id").values_list("question", flat=True)[:40]
        )
        difficulty = normalise_difficulty(data.get("difficulty"))
        bank_set = question_sets.serve(request.user, role, difficulty)
        if bank_set is None:
            try:
                questions = question_sets.generate(role, difficulty, previous_questions)
            except GeminiAPIError as error:
                status = 503 if "not configured" in str(error) else 502
                return JsonResponse({"error": str(error)}, status=status)
            except question_sets.InvalidQuestions as error:
                return JsonResponse({"error": str(error)}, status=502)
            bank_set = question_sets.store(role, difficulty, questions)
        session = MockInterviewSession.objects.create(
            student=request.user, role=role, difficulty=difficulty,
            question_set=bank_set,
        )
        return JsonResponse({"questions": bank_set.questions, "session_id": session.pk})
    else:
        session = MockInterviewSession.objects.filter(
            pk=session_id, student=request.user
        ).first()
        if session is None:
            return JsonResponse({"error": "Interview session not found."}, status=404)
        question_number = data.get("question_number")
        question = data.get("question", "")
        if type(question_number) is not int or not 1 <= question_number <= MOCK_QUESTION_COUNT:
            return JsonResponse({"error": "Invalid question number."}, status=400)
        if not isinstance(question, str) or not question.strip():
            return JsonResponse({"error": "The interview question is missing."}, status=400)
        role = session.role
        if question_number > session.expected_answers:
            session.expected_answers = question_number
            session.save(update_fields=["expected_answers"])
        audio = data.get("audio", "")
        frames = data.get("frames", {})
        code = data.get("code")
        text_answer = data.get("text")
        lite = data.get("lite") is True
        audio_match = None
        audio_bytes = b""
        if code is not None:
            language = data.get("language")
            code_output = data.get("code_output", "")
            if (
                not isinstance(code, str) or not code.strip() or len(code) > 20_000
                or language not in {"python", "sql", "pyspark"}
                or not isinstance(code_output, str)
            ):
                return JsonResponse({"error": "Write your code before submitting."}, status=400)
        elif text_answer is not None:
            if (
                not lite or not isinstance(text_answer, str)
                or not text_answer.strip() or len(text_answer) > 3000
            ):
                return JsonResponse({"error": "Type your answer before sending."}, status=400)
        else:
            audio_match = re.fullmatch(
                r"data:(audio/(?:webm|mp4|ogg|wav|mpeg|mp3|aac|flac|opus|aiff|m4a))"
                r"(?:;codecs=[A-Za-z0-9.-]+)?;base64,([A-Za-z0-9+/=]+)",
                audio,
            ) if isinstance(audio, str) else None
            if not audio_match or len(audio_match.group(2)) > 600_000:
                return JsonResponse({"error": "Record a valid answer up to 60 seconds long."}, status=400)
            try:
                audio_bytes = base64.b64decode(audio_match.group(2), validate=True)
            except ValueError:
                return JsonResponse({"error": "The recorded answer is invalid."}, status=400)
            if not audio_bytes:
                return JsonResponse({"error": "The recording is empty. Record your answer again."}, status=400)
        if not lite and not isinstance(frames, dict):
            return JsonResponse({"error": "Camera and screen snapshots are required."}, status=400)

        if code is not None:
            parts = [{"text": (
                "Evaluate this coding answer from a mock interview. Assess correctness, edge cases, "
                "efficiency, readability and whether the output matches the question. "
                "Keep each feedback field to one concise sentence of at most 20 words. Do not infer "
                "identity, age, gender, race, health, or personality. Use the camera image only for "
                "framing, lighting, and visibility. Use the screen image only for readability and "
                "relevance to the role. Return JSON with integer score from 1 to 5, "
                "and strings answer_feedback, camera_feedback, and screen_feedback.\n"
                f"Topic: {role}\nQuestion {question_number}: {question[:500]}\n"
                f"Language: {language}\nCandidate code:\n{code}\n"
                f"Output from the candidate's last run:\n{code_output[:2000] or '(not run)'}"
            )}]
        elif text_answer is not None:
            parts = [{"text": (
                "Evaluate this typed mock interview answer. Assess relevance, correctness, "
                "completeness, structure and clarity. Keep answer_feedback to one concise sentence "
                "of at most 20 words. Do not infer identity, age, gender, race, health, or personality. "
                "Return JSON with integer score from 1 to 5 and string answer_feedback.\n"
                f"Topic: {role}\nQuestion {question_number}: {question[:500]}\n"
                f"Candidate answer:\n{text_answer}"
            )}]
        else:
            parts = [{"text": (
                "Evaluate this recorded mock interview answer. Transcribe the speech verbatim and assess "
                "relevance, correctness, completeness, structure, clarity, and verbal delivery. "
                "Keep each feedback field to one concise sentence of at most 20 words. Do not judge "
                "accent or infer identity, age, gender, race, health, or personality. Use the camera image "
                "only for framing, lighting, and visibility. Use the screen image only for readability and "
                "relevance to the role. Return JSON with answer_transcript, integer score from 1 to 5, "
                "and strings answer_feedback, camera_feedback, and screen_feedback.\n"
                f"Role: {role}\nQuestion {question_number}: {question[:500]}"
            )}, {
                "inlineData": {"mimeType": audio_match.group(1), "data": audio_match.group(2)},
            }]
        for frame_name, label in (() if lite else (("camera", "Camera snapshot"), ("screen", "Shared-screen snapshot"))):
            frame = frames.get(frame_name, "")
            match = re.fullmatch(
                r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", frame
            ) if isinstance(frame, str) else None
            if not match or len(match.group(1)) > 400_000:
                return JsonResponse({"error": "Camera and screen snapshots are required."}, status=400)
            try:
                image_bytes = base64.b64decode(match.group(1), validate=True)
            except ValueError:
                return JsonResponse({"error": "Invalid image snapshot."}, status=400)
            if not image_bytes.startswith(b"\xff\xd8\xff"):
                return JsonResponse({"error": "Invalid image snapshot."}, status=400)
            parts.extend([
                {"text": label},
                {"inlineData": {"mimeType": "image/jpeg", "data": match.group(1)}},
            ])

        existing_score = MockInterviewScore.objects.filter(
            session=session, question_number=question_number
        ).first()
        if existing_score and existing_score.status in {
            MockInterviewScore.PENDING, MockInterviewScore.COMPLETE,
        }:
            return JsonResponse({"error": "This answer has already been submitted."}, status=409)
        score_record, _ = MockInterviewScore.objects.update_or_create(
            session=session,
            question_number=question_number,
            defaults={
                "question": question[:500],
                "status": MockInterviewScore.PENDING,
                "score": None,
                "answer_transcript": "",
                "answer_feedback": "",
                "camera_feedback": "",
                "screen_feedback": "",
                "payload": None,
                "attempts": 0,
                "next_attempt_at": None,
                "claimed_at": None,
            },
        )

    score_record.payload = {"parts": parts, "audio": audio_match is not None, "lite": lite}
    score_record.save(update_fields=["payload"])
    return JsonResponse({"status": "queued", "question_number": question_number})
