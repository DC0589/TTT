import datetime

from django.db import models

from .users import User


class Attendance(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="attendance_records")
    date = models.DateField()
    check_in = models.DateTimeField()
    check_out = models.DateTimeField(null=True, blank=True)
    check_in_lat = models.FloatField(null=True, blank=True)
    check_in_lng = models.FloatField(null=True, blank=True)
    check_in_accuracy = models.FloatField(null=True, blank=True)
    check_out_lat = models.FloatField(null=True, blank=True)
    check_out_lng = models.FloatField(null=True, blank=True)
    check_out_accuracy = models.FloatField(null=True, blank=True)
    is_late = models.BooleanField(default=False)
    outside_geofence = models.BooleanField(default=False)
    check_out_outside_geofence = models.BooleanField(default=False)
    distance_m = models.PositiveIntegerField(null=True, blank=True)
    auto_closed = models.BooleanField(default=False)

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

    @staticmethod
    def _map_url(lat, lng):
        if lat is None or lng is None:
            return ""
        return f"https://www.google.com/maps?q={lat:.6f},{lng:.6f}"

    @property
    def check_in_map_url(self):
        return self._map_url(self.check_in_lat, self.check_in_lng)

    @property
    def check_out_map_url(self):
        return self._map_url(self.check_out_lat, self.check_out_lng)

    def __str__(self):
        return f"{self.student} - {self.date}"


class AttendanceSettings(models.Model):
    """Per-admin attendance rules: office location, late cut-off and auto check-out."""
    admin = models.OneToOneField(User, on_delete=models.CASCADE, related_name="attendance_settings")
    centre_lat = models.FloatField(null=True, blank=True)
    centre_lng = models.FloatField(null=True, blank=True)
    radius_m = models.PositiveIntegerField(default=200)
    block_outside = models.BooleanField(default=False)
    late_after = models.TimeField(default=datetime.time(10, 0))
    auto_close_at = models.TimeField(default=datetime.time(18, 0))

    @property
    def geofence_enabled(self):
        return self.centre_lat is not None and self.centre_lng is not None

    def __str__(self):
        return f"Attendance settings for {self.admin}"


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


class LoginLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_logs")
    session_key = models.CharField(max_length=40, blank=True, db_index=True)
    logged_in_at = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now_add=True)
    logged_out_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-logged_in_at"]
        indexes = [models.Index(fields=["-logged_in_at"]), models.Index(fields=["logged_out_at", "last_seen"])]

    def __str__(self):
        return f"{self.user} @ {self.logged_in_at:%Y-%m-%d %H:%M}"
