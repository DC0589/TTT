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


def for_login(request):
    """Announcements to pop up once per login session."""
    session = getattr(request, "session", None)
    if session is None:
        return active_for(request.user)
    if session.get("_announcements_shown"):
        return []
    items = active_for(request.user)
    if items:
        session["_announcements_shown"] = True
    return items
