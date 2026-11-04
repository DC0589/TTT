"""Lock student accounts after INACTIVITY_LOCK_DAYS inactive working days."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Max, Q
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from ..models import LeaveRequest, LoginLog, StudentBreak, User

LOCKED_MESSAGE = (
    "Your account is locked because you did not log in for {days} working days. "
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


def _owner_admin_ids(student):
    admin_ids = set(student.student_groups.values_list("admin_id", flat=True))
    creator = student.created_by
    if creator:
        if creator.is_admin:
            admin_ids.add(creator.pk)
        elif creator.created_by_id:
            admin_ids.add(creator.created_by_id)
    return {
        admin.shares_data_with_id or admin.pk
        for admin in User.objects.filter(pk__in=admin_ids, is_admin=True).only(
            "pk", "shares_data_with_id"
        )
    }


def _date_range(start, end):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _inactive_working_days(student, last_active, now):
    first_day = timezone.localdate(last_active) + timedelta(days=1)
    last_day = timezone.localdate(now)
    if first_day > last_day:
        return 0

    excluded = set()
    for start, end in LeaveRequest.objects.filter(
        student=student,
        status=LeaveRequest.APPROVED,
        start_date__lte=last_day,
        end_date__gte=first_day,
    ).values_list("start_date", "end_date"):
        excluded.update(_date_range(max(start, first_day), min(end, last_day)))

    owner_ids = _owner_admin_ids(student)
    if owner_ids:
        for start, end in StudentBreak.objects.filter(
            admin_id__in=owner_ids,
            start_date__lte=last_day,
            end_date__gte=first_day,
        ).values_list("start_date", "end_date"):
            excluded.update(_date_range(max(start, first_day), min(end, last_day)))

    return sum(
        day.weekday() < 5 and day not in excluded
        for day in _date_range(first_day, last_day)
    )


def _has_exceeded_inactivity(student, last_active, now):
    if now - last_active < inactivity_limit():
        return False
    return _inactive_working_days(student, last_active, now) >= settings.INACTIVITY_LOCK_DAYS


def lock_if_inactive(user, now=None):
    """Lock a student after enough inactive working days. Returns whether it is locked."""
    if not user.is_student or user.is_admin or user.is_hr:
        return False
    if user.is_locked:
        return True
    now = now or timezone.now()
    if _has_exceeded_inactivity(user, last_activity(user), now):
        lock(user)
        return True
    return False


def lock_inactive_students():
    """Lock inactive students, excluding weekends and approved/scheduled leave."""
    now = timezone.now()
    candidates = (
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
    )
    stale_ids = [
        student.pk
        for student in candidates
        if _has_exceeded_inactivity(student, student.last_active, now)
    ]
    return User.objects.filter(pk__in=stale_ids).update(is_locked=True, locked_at=now)


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
