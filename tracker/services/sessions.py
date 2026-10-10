"""Login activity: who signed in, when, and who is online right now."""
from datetime import timedelta

from django.utils import timezone

from ..models import LoginLog

ONLINE_WINDOW = timedelta(minutes=5)
HEARTBEAT_INTERVAL = timedelta(seconds=60)


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR", "")
    return ip or None


def _session_key(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ""


def start_session(request, user):
    return LoginLog.objects.create(
        user=user,
        session_key=_session_key(request),
        ip_address=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
    )


def end_session(request, user):
    key = request.session.session_key
    if key:
        LoginLog.objects.filter(
            user=user, session_key=key, logged_out_at__isnull=True
        ).update(logged_out_at=timezone.now())


def heartbeat(request):
    """Refresh last_seen at most once per HEARTBEAT_INTERVAL for the current session."""
    now = timezone.now()
    last = request.session.get("_seen_at")
    if last and now.timestamp() - last < HEARTBEAT_INTERVAL.total_seconds():
        return
    key = _session_key(request)
    updated = LoginLog.objects.filter(
        user=request.user, session_key=key, logged_out_at__isnull=True
    ).update(last_seen=now)
    if not updated:
        start_session(request, request.user)
    request.session["_seen_at"] = now.timestamp()


def visible_users(staff):
    from ..models import User
    qs = User.objects.all()
    if staff.is_admin:
        return qs
    return qs.filter(is_student=True)


def online_logs(staff):
    cutoff = timezone.now() - ONLINE_WINDOW
    return (
        LoginLog.objects.filter(
            user__in=visible_users(staff), logged_out_at__isnull=True, last_seen__gte=cutoff
        )
        .select_related("user")
        .order_by("-last_seen")
    )


def history(staff, user_id=None):
    qs = LoginLog.objects.filter(user__in=visible_users(staff)).select_related("user")
    if user_id:
        qs = qs.filter(user_id=user_id)
    return qs
