"""Lock student accounts that have not been used for INACTIVITY_LOCK_DAYS days."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Max, Q
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from ..models import LoginLog, User

LOCKED_MESSAGE = (
    "Your account is locked because you did not log in for {days} days. "
    "Please contact your administrator or HR to unlock it."
)


def inactivity_limit():
    return timedelta(days=settings.INACTIVITY_LOCK_DAYS)


def locked_message():
    return LOCKED_MESSAGE.format(days=settings.INACTIVITY_LOCK_DAYS)


def last_activity(user):
    """Most recent sign-in, activity heartbeat, unlock or account creation."""
    seen = LoginLog.objects.filter(user=user).aggregate(latest=Max("last_seen"))["latest"]
    return max(t for t in (user.last_login, user.date_joined, user.unlocked_at, seen) if t)


def lock(user):
    user.is_locked = True
    user.locked_at = timezone.now()
    user.save(update_fields=["is_locked", "locked_at"])


def lock_if_inactive(user):
    """Lock a student who has been away too long. Returns True when the account is locked."""
    if not user.is_student or user.is_admin or user.is_hr:
        return False
    if user.is_locked:
        return True
    if timezone.now() - last_activity(user) >= inactivity_limit():
        lock(user)
        return True
    return False


def lock_inactive_students():
    """Lock every inactive student in one pass. Returns the number newly locked."""
    now = timezone.now()
    stale = (
        User.objects.filter(is_student=True, is_admin=False, is_hr=False, is_locked=False)
        .annotate(
            latest_seen=Max("login_logs__last_seen"),
            last_active=Greatest(
                "date_joined",
                Coalesce("last_login", "date_joined"),
                Coalesce("unlocked_at", "date_joined"),
                Coalesce("latest_seen", "date_joined"),
            ),
        )
        .filter(last_active__lte=now - inactivity_limit())
        .values_list("pk", flat=True)
    )
    return User.objects.filter(pk__in=list(stale)).update(is_locked=True, locked_at=now)


def unlock(user):
    user.is_locked = False
    user.locked_at = None
    user.unlocked_at = timezone.now()
    user.save(update_fields=["is_locked", "locked_at", "unlocked_at"])


def managed_students(staff):
    """Students an admin or HR user may unlock."""
    if staff.is_admin:
        return User.objects.filter(is_student=True).filter(
            Q(memberships__group__admin=staff.data_owner)
            | Q(created_by__in=staff.team_ids)
            | Q(created_by__created_by__in=staff.team_ids)
        ).distinct()
    if staff.created_by_id:
        return User.objects.filter(is_student=True).filter(
            Q(created_by=staff) | Q(memberships__group__admin=staff.data_owner)
        ).distinct()
    return User.objects.filter(is_student=True, created_by=staff)
