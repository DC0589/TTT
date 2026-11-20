from datetime import timedelta
import logging

from django.core.cache import cache
from django.db import DatabaseError, transaction
from django.utils import timezone

from .models import GroupMembership, InterviewStatus, LeaveRequest, Selection, StudentQuestionSubmission
from .services.placements import placed_company_names

logger = logging.getLogger(__name__)
BANNER_CACHE_KEY = "celebration-banner-data"
BANNER_CACHE_SECONDS = 300
CELEBRATION_DAYS = 7
MIN_ITEMS_PER_LOOP = 12
PLACED_SECONDS_PER_ITEM = 3
SELECTED_SECONDS_PER_ITEM = 12


def _optional_data(label, load):
    try:
        with transaction.atomic():
            return load()
    except DatabaseError:
        logger.exception("Unable to load optional %s", label)
        return None


def _loop(count, seconds_per_item):
    repeats = max(1, -(-MIN_ITEMS_PER_LOOP // max(count, 1)))
    return {
        "repeats": range(repeats),
        "duration": max(20, repeats * count * seconds_per_item),
    }


def _banner_data():
    data = cache.get(BANNER_CACHE_KEY)
    if data is None:
        data = _build_banner_data()
        cache.set(BANNER_CACHE_KEY, data, BANNER_CACHE_SECONDS)
    return data


def _build_banner_data():
    selected = InterviewStatus.objects.filter(
        final_status=InterviewStatus.SELECTED
    ).select_related("interview__student")

    placed = placed_company_names()

    since = timezone.now() - timedelta(days=CELEBRATION_DAYS)
    recent = [
        (s.updated_at, {
            "student": s.interview.student.get_full_name() or s.interview.student.username,
            "company": s.interview.company_name,
            "role": s.interview.role,
        })
        for s in selected.filter(updated_at__gte=since)
    ]
    recent += [
        (s.created_at, {
            "student": s.student.get_full_name() or s.student.username,
            "company": s.company,
            "role": s.role,
        })
        for s in Selection.objects.filter(created_at__gte=since).select_related("student")
    ]
    recent = [item for _, item in sorted(recent, key=lambda x: x[0], reverse=True)[:20]]
    return {"placed": placed, "recent": recent}


def celebration_banners(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    banners = _optional_data("celebration banners", _banner_data)
    result = {}
    if banners is None:
        result["banner_unavailable"] = True
    else:
        placed, recent = banners["placed"], banners["recent"]
        result.update({
            "placed_companies": placed,
            "placed_loop": _loop(len(placed) + 1, PLACED_SECONDS_PER_ITEM),
            "recent_selections": recent,
            "selected_loop": _loop(len(recent), SELECTED_SECONDS_PER_ITEM),
        })

    if user.is_admin or user.is_hr:
        def leave_count():
            owner = user.data_owner
            return LeaveRequest.objects.filter(
                status=LeaveRequest.PENDING,
                student__in=GroupMembership.objects.filter(group__admin=owner).values("student"),
            ).count()

        pending_leaves = _optional_data("leave notification count", leave_count)
        pending_questions = _optional_data(
            "question notification count",
            lambda: StudentQuestionSubmission.objects.filter(status=StudentQuestionSubmission.PENDING).count(),
        )
        if pending_leaves is None:
            result["leave_count_unavailable"] = True
        else:
            result["pending_leave_count"] = pending_leaves
        if pending_questions is None:
            result["question_count_unavailable"] = True
        else:
            result["pending_question_count"] = pending_questions
    return result


def support_unread(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    from .services import support
    if user.is_student:
        count = _optional_data("support notification count", lambda: int(support.student_has_unread(user)))
    elif user.is_admin or user.is_hr:
        count = _optional_data("support notification count", lambda: support.unread_for_staff(user))
    else:
        return {}
    if count is None:
        return {"support_count_unavailable": True}
    return {"support_unread": count}
