from django.conf import settings
from django.db import models


class SupportThread(models.Model):
    """One direct conversation per student, shared by the staff who manage that student."""
    student = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_thread")
    last_message_at = models.DateTimeField(blank=True, null=True, db_index=True)
    student_seen_at = models.DateTimeField(blank=True, null=True)
    staff_seen_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-last_message_at"]


class SupportMessage(models.Model):
    thread = models.ForeignKey(SupportThread, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_messages")
    from_student = models.BooleanField()
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
