from datetime import timedelta

from django.utils import timezone

from .models import InterviewStatus, PlacedStudent

CELEBRATION_DAYS = 7


def celebration_banners(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}

    selected = InterviewStatus.objects.filter(
        final_status=InterviewStatus.SELECTED
    ).select_related("interview__student")

    companies = {}
    for company in selected.values_list("interview__company_name", flat=True):
        name = company.strip()
        if name:
            companies.setdefault(name.lower(), name)

    for company in PlacedStudent.objects.values_list("company", flat=True):
        companies.setdefault(company.strip().lower(), company.strip())

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
        "placed_companies": sorted(companies.values(), key=str.lower),
        "recent_selections": recent,
    }
