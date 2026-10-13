from django.db import models
from django.utils import timezone

from .users import User


class Announcement(models.Model):
    """A pop-up message shown on the overview page between two moments."""
    ALL, STUDENTS, STAFF = "all", "students", "staff"
    AUDIENCES = [(ALL, "Everyone"), (STUDENTS, "Students only"), (STAFF, "Admin and HR only")]

    title = models.CharField(max_length=120)
    message = models.TextField(max_length=1000)
    audience = models.CharField(max_length=10, choices=AUDIENCES, default=ALL)
    start_at = models.DateTimeField(default=timezone.now)
    end_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_at", "-id"]

    def __str__(self):
        return self.title

    @property
    def status(self):
        now = timezone.now()
        if not self.is_active:
            return "Paused"
        if now < self.start_at:
            return "Scheduled"
        return "Live" if now <= self.end_at else "Ended"
