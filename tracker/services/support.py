from django.db.models import F, Q
from django.utils import timezone

from ..models import SupportMessage, SupportThread
from . import account_lock

MAX_LENGTH = 2000


def thread_for(student):
    return SupportThread.objects.get_or_create(student=student)[0]


def post(thread, sender, body):
    body = (body or "").strip()[:MAX_LENGTH]
    if not body:
        return None
    from_student = sender.id == thread.student_id
    message = SupportMessage.objects.create(thread=thread, sender=sender, from_student=from_student, body=body)
    seen = "student_seen_at" if from_student else "staff_seen_at"
    SupportThread.objects.filter(pk=thread.pk).update(last_message_at=message.created_at, **{seen: message.created_at})
    return message


def mark_seen(thread, as_student):
    SupportThread.objects.filter(pk=thread.pk).update(
        **{"student_seen_at" if as_student else "staff_seen_at": timezone.now()}
    )


def serialize(messages):
    return [
        {
            "id": m.id,
            "from_student": m.from_student,
            "sender": m.sender.get_full_name() or m.sender.username,
            "role": "Student" if m.from_student else ("Admin" if m.sender.is_admin else "HR"),
            "body": m.body,
            "at": timezone.localtime(m.created_at).strftime("%d %b, %H:%M"),
        }
        for m in messages
    ]


def messages_after(thread, after_id=0):
    return thread.messages.filter(id__gt=after_id).select_related("sender")


def unread_for_staff(staff):
    """Managed students whose conversation has messages staff haven't opened yet."""
    ids = account_lock.managed_students(staff).values("pk")
    return SupportThread.objects.filter(student_id__in=ids, last_message_at__isnull=False).filter(
        Q(staff_seen_at__isnull=True) | Q(staff_seen_at__lt=F("last_message_at"))
    ).count()


def student_has_unread(student):
    return SupportThread.objects.filter(student=student, last_message_at__isnull=False).filter(
        Q(student_seen_at__isnull=True) | Q(student_seen_at__lt=F("last_message_at"))
    ).exists()
