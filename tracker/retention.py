from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import MockInterviewSession


def purge_old_mock_data(days=None):
    """Remove detailed integrity logs older than the retention window.

    Ratings, per-question scores and feedback are kept; each purged session
    keeps its integrity score summary.
    """
    days = settings.MOCK_RETENTION_DAYS if days is None else days
    if days <= 0:
        return 0
    cutoff = timezone.now() - timedelta(days=days)
    purged = 0
    stale = MockInterviewSession.objects.filter(
        created_at__lt=cutoff, events_purged_at__isnull=True,
    ).exclude(integrity_events=[])
    for session in stale.iterator():
        session.purge_integrity_events()
        purged += 1
    # Sessions with no flags still get marked so their (perfect) score stays fixed.
    MockInterviewSession.objects.filter(
        created_at__lt=cutoff, events_purged_at__isnull=True, integrity_events=[],
    ).update(events_purged_at=timezone.now(), integrity_summary={"score": 100, "flags": 0, "auto_ended": False})
    return purged
