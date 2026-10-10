from datetime import timedelta

from django.utils import timezone

from .models import InterviewStatus, PlacedStudent, Selection

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


def celebration_banners(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    selected = InterviewStatus.objects.filter(
        final_status=InterviewStatus.SELECTED
    ).select_related("interview__student")

    companies = {}
    for company in PlacedStudent.objects.values_list("company", flat=True):
        name = company.strip()
        if name:
            companies.setdefault(name.lower(), name)
    placed = sorted(companies.values(), key=str.lower)

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

    return {
        "placed_companies": placed,
        "placed_loop": _loop(len(placed) + 1, PLACED_SECONDS_PER_ITEM),
        "recent_selections": recent,
        "selected_loop": _loop(len(recent), SELECTED_SECONDS_PER_ITEM),
    }
