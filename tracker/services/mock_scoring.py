"""Deferred scoring of mock interview answers.

Answers are saved as pending with their raw payload and scored here at a steady
rate, so a class answering together never exceeds the AI quota.
"""
import logging
import time
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Avg, F, Q
from django.utils import timezone

from ..ai_interview import GeminiAPIError, generate_json
from ..models import MockInterviewScore

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 8
CLAIM_TIMEOUT = timedelta(seconds=90)
PAYLOAD_KEEP = timedelta(days=3)
FAILED_MESSAGE = "Feedback could not be generated. Please try again later."


def _recent_attempts():
    since = timezone.now() - timedelta(seconds=60)
    return MockInterviewScore.objects.filter(last_attempt_at__gte=since).count()


def _claim(score):
    """Atomically take a pending row so two workers never score it twice."""
    now = timezone.now()
    claimed = MockInterviewScore.objects.filter(
        pk=score.pk, status=MockInterviewScore.PENDING, payload__isnull=False,
    ).filter(
        Q(claimed_at__isnull=True) | Q(claimed_at__lt=now - CLAIM_TIMEOUT)
    ).update(claimed_at=now, last_attempt_at=now, attempts=F("attempts") + 1)
    return bool(claimed)


def _apply_result(score, result):
    payload = score.payload or {}
    value = result.get("score", 3)
    value = max(1, min(value if type(value) is int else 3, 5))
    transcript = result.get("answer_transcript", "") if payload.get("audio") else ""
    score.status = MockInterviewScore.COMPLETE
    score.score = value
    score.answer_transcript = transcript.strip()[:5000] if isinstance(transcript, str) else ""
    score.answer_feedback = str(result.get("answer_feedback", "Review your answer and try again."))[:800]
    if payload.get("lite"):
        score.camera_feedback = score.screen_feedback = ""
    else:
        score.camera_feedback = str(result.get("camera_feedback", "No camera feedback available."))[:500]
        score.screen_feedback = str(result.get("screen_feedback", "No screen feedback available."))[:500]
    score.payload = None
    score.claimed_at = None
    score.next_attempt_at = None
    score.save(update_fields=[
        "status", "score", "answer_transcript", "answer_feedback", "camera_feedback",
        "screen_feedback", "payload", "claimed_at", "next_attempt_at",
    ])
    session = score.session
    average = session.scores.filter(
        status=MockInterviewScore.COMPLETE, score__isnull=False
    ).aggregate(average=Avg("score"))["average"]
    if average is not None:
        session.rating = Decimal(str(average)).quantize(Decimal("0.01"))
        session.save(update_fields=["rating"])


def _record_failure(score, error):
    score.claimed_at = None
    if score.attempts >= MAX_ATTEMPTS or "not configured" in str(error):
        score.status = MockInterviewScore.FAILED
        score.answer_feedback = FAILED_MESSAGE
        score.next_attempt_at = None
    else:
        score.next_attempt_at = timezone.now() + timedelta(seconds=min(30 * score.attempts, 240))
    score.save(update_fields=[
        "status", "answer_feedback", "payload", "claimed_at", "next_attempt_at",
    ])


def score_one(score, deadline=None):
    try:
        result = generate_json((score.payload or {}).get("parts", []), deadline=deadline)
    except GeminiAPIError as error:
        logger.warning("Mock answer %s scoring failed: %s", score.pk, error)
        _record_failure(score, error)
        return False
    _apply_result(score, result)
    return True


def process_pending(max_items=3, session=None, time_budget=40):
    """Score a few due answers, respecting the per-minute budget. Returns the count scored."""
    now = timezone.now()
    due = MockInterviewScore.objects.filter(
        status=MockInterviewScore.PENDING, payload__isnull=False,
    ).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    budget = settings.MOCK_SCORING_PER_MINUTE - _recent_attempts()
    started = time.monotonic()
    scored = 0
    candidates = list(due.select_related("session").order_by("id")[:50])
    if session is not None:
        candidates.sort(key=lambda row: row.session_id != session.pk)
    for score in candidates[: max(0, min(max_items, budget))]:
        if time.monotonic() - started > time_budget:
            break
        if not _claim(score):
            continue
        score.refresh_from_db()
        remaining = time_budget - (time.monotonic() - started)
        if remaining < 4:
            break
        if score_one(score, deadline=remaining):
            scored += 1
    return scored


def retry_failed(session):
    """Put a student's failed answers back in the queue (only those still holding their answer)."""
    return MockInterviewScore.objects.filter(
        session=session, status=MockInterviewScore.FAILED, payload__isnull=False,
    ).update(
        status=MockInterviewScore.PENDING, attempts=0, next_attempt_at=None,
        claimed_at=None, last_attempt_at=None, answer_feedback="",
    )


def pending_count():
    return MockInterviewScore.objects.filter(
        status=MockInterviewScore.PENDING, payload__isnull=False
    ).count()


def purge_old_payloads():
    """Drop stored answers that were never scored so media does not fill the database."""
    return MockInterviewScore.objects.filter(
        payload__isnull=False,
        status=MockInterviewScore.FAILED,
        last_attempt_at__lt=timezone.now() - PAYLOAD_KEEP,
    ).update(payload=None)
