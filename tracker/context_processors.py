from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from .models import GroupMembership, InterviewStatus, LeaveRequest, Selection
from .services.placements import placed_company_names

BANNER_CACHE_KEY = "celebration-banner-data"
BANNER_CACHE_SECONDS = 300
CELEBRATION_DAYS = 7
MIN_ITEMS_PER_LOOP = 12
PLACED_SECONDS_PER_ITEM = 3
SELECTED_SECONDS_PER_ITEM = 12


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

    banners = _banner_data()
    placed, recent = banners["placed"], banners["recent"]

    pending_leaves = 0
    if user.is_admin or user.is_hr:
        owner = user.data_owner
        pending_leaves = LeaveRequest.objects.filter(
            status=LeaveRequest.PENDING,
            student__in=GroupMembership.objects.filter(group__admin=owner).values("student"),
        ).count()

    return {
        "pending_leave_count": pending_leaves,
        "placed_companies": placed,
        "placed_loop": _loop(len(placed) + 1, PLACED_SECONDS_PER_ITEM),
        "recent_selections": recent,
        "selected_loop": _loop(len(recent), SELECTED_SECONDS_PER_ITEM),
    }
