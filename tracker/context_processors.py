from datetime import timedelta

from django.utils import timezone

from .models import InterviewStatus, PlacedStudent

CELEBRATION_DAYS = 7
MIN_ITEMS_PER_LOOP = 12
SECONDS_PER_ITEM = 3


def _loop(count):
    repeats = max(1, -(-MIN_ITEMS_PER_LOOP // max(count, 1)))
    return {
        "repeats": range(repeats),
        "duration": max(20, repeats * count * SECONDS_PER_ITEM),
    }


def celebration_banners(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    selected = InterviewStatus.objects.filter(
        final_status=InterviewStatus.SELECTED
    ).select_related("interview__student")

    companies = {}
    names = list(selected.values_list("interview__company_name", flat=True))
    names += list(PlacedStudent.objects.values_list("company", flat=True))
    for company in names:
        name = company.strip()
        if name:
            companies.setdefault(name.lower(), name)
    placed = sorted(companies.values(), key=str.lower)

    since = timezone.now() - timedelta(days=CELEBRATION_DAYS)
    recent = [
        {
            "student": s.interview.student.get_full_name() or s.interview.student.username,
            "company": s.interview.company_name,
            "role": s.interview.role,
        }
        for s in selected.filter(updated_at__gte=since).order_by("-updated_at")[:20]
    ]

    return {
        "placed_companies": placed,
        "placed_loop": _loop(len(placed) + 1),
        "recent_selections": recent,
        "selected_loop": _loop(len(recent)),
    }
