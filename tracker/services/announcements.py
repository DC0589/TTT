from django.db.models import Q
from django.utils import timezone

from ..models import Announcement


def active_for(user):
    """Announcements that are live right now for this user's role."""
    now = timezone.now()
    audience = Announcement.STAFF if (user.is_admin or user.is_hr) else Announcement.STUDENTS
    return list(
        Announcement.objects.filter(
            is_active=True, start_at__lte=now, end_at__gte=now,
        ).filter(Q(audience=Announcement.ALL) | Q(audience=audience)).order_by("start_at", "id")[:10]
    )
