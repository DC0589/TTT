from django.db import models

from .users import User


class Attendance(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    check_in = models.DateTimeField()
    check_out = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["date"])]
        constraints = [
            models.UniqueConstraint(fields=["student", "date"], name="unique_attendance_per_day"),
        ]

    @property
    def duration(self):
        if not self.check_out:
            return None
        minutes = int((self.check_out - self.check_in).total_seconds() // 60)
        return f"{minutes // 60}h {minutes % 60:02d}m"

    def __str__(self):
        return f"{self.student} - {self.date}"


class LeaveRequest(models.Model):
    PENDING, APPROVED, REJECTED, CANCELLED = "pending", "approved", "rejected", "cancelled"
    STATUS_CHOICES = [
        (PENDING, "Pending"), (APPROVED, "Approved"),
        (REJECTED, "Rejected"), (CANCELLED, "Cancelled"),
    ]

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="leave_requests")
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField(max_length=1000)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    reviewed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="leaves_reviewed"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "start_date"])]

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1

    @property
    def badge(self):
        return {
            self.PENDING: "status-pending", self.APPROVED: "status-selected",
            self.REJECTED: "status-rejected", self.CANCELLED: "status-not-selected",
        }[self.status]

    def __str__(self):
        return f"{self.student}: {self.start_date} to {self.end_date} ({self.status})"
