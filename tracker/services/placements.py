from django.utils import timezone

from ..models import Company, PlacedStudent


def clean_name(value):
    return " ".join((value or "").split())


def record_placement(student, company, batch=""):
    """Add a student to the placed list once per company (case-insensitive)."""
    company = clean_name(company)
    if not company:
        return None
    name = student.get_full_name() or student.username
    ref = Company.objects.for_name(company)
    if PlacedStudent.objects.filter(name__iexact=name, company_ref=ref).exists():
        return None
    return PlacedStudent.objects.create(
        name=name[:150], company=company[:150], batch=(batch or "")[:40],
        year=timezone.localdate().year,
    )


def import_placements(entries):
    """Bulk-add parsed placement dicts (name, batch, company, year), skipping duplicates.

    Returns (created_count, skipped_count).
    """
    existing = set(
        (n.lower(), key)
        for n, key in PlacedStudent.objects.values_list("name", "company_ref__key")
    )
    companies = {}
    new_rows = []
    for entry in entries:
        ref = Company.objects.for_name(entry["company"])
        if ref is None:
            continue
        marker = (entry["name"].lower(), ref.key)
        if marker in existing:
            continue
        existing.add(marker)
        companies[ref.key] = ref
        new_rows.append(PlacedStudent(company_ref=ref, **entry))
    PlacedStudent.objects.bulk_create(new_rows)
    return len(new_rows), len(entries) - len(new_rows)


def placed_company_names():
    """Distinct placed companies for the banner, one display name each."""
    return list(
        Company.objects.filter(placed_students__isnull=False)
        .distinct()
        .order_by("name")
        .values_list("name", flat=True)
    )


def top_companies(limit=8):
    from django.db.models import Count

    return list(
        Company.objects.annotate(total=Count("placed_students"))
        .filter(total__gt=0)
        .order_by("-total", "name")[:limit]
    )
