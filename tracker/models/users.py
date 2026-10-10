from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    is_admin = models.BooleanField(default=False)
    is_student = models.BooleanField(default=False)
    is_hr = models.BooleanField(default=False)
    referred_by = models.CharField(max_length=150, blank=True)
    mobile_number = models.CharField(max_length=30, blank=True)
    graduation = models.CharField(max_length=150, blank=True)
    department = models.CharField(max_length=150, blank=True)
    hometown = models.CharField(max_length=150, blank=True)
    parent_name = models.CharField(max_length=150, blank=True)
    parent_mobile_number = models.CharField(max_length=30, blank=True)
    skills = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "self",
        blank=True,
        limit_choices_to={"is_admin": True},
        null=True,
        on_delete=models.SET_NULL,
        related_name="students_created",
    )

    @property
    def data_owner(self):
        """The admin whose data this user works with (an HR user's creating admin)."""
        return (self.created_by or self) if self.is_hr else self

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.is_admin = True
        if self.is_admin:
            self.is_hr = False
        super().save(*args, **kwargs)


class StudentRegistrationRequest(models.Model):
    AWAITING_VERIFICATION = "awaiting_verification"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    STATUS_CHOICES = [
        (AWAITING_VERIFICATION, "Awaiting email verification"),
        (AWAITING_APPROVAL, "Awaiting admin approval"),
        (APPROVED, "Approved"),
        (REJECTED, "Rejected"),
        (EXPIRED, "Expired"),
    ]

    username = models.CharField(max_length=150)
    email = models.EmailField()
    referred_by = models.CharField(max_length=150, blank=True)
    mobile_number = models.CharField(max_length=30, blank=True)
    graduation = models.CharField(max_length=150, blank=True)
    department = models.CharField(max_length=150, blank=True)
    hometown = models.CharField(max_length=150, blank=True)
    parent_name = models.CharField(max_length=150, blank=True)
    parent_mobile_number = models.CharField(max_length=30, blank=True)
    skills = models.TextField(blank=True)
    password_hash = models.CharField(max_length=128)
    verification_code_hash = models.CharField(max_length=64)
    verification_attempts = models.PositiveSmallIntegerField(default=0)
    verification_resend_count = models.PositiveSmallIntegerField(default=0)
    verification_last_sent_at = models.DateTimeField(blank=True, null=True)
    verification_expires_at = models.DateTimeField()
    status = models.CharField(
        max_length=24, choices=STATUS_CHOICES, default=AWAITING_VERIFICATION
    )
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(blank=True, null=True)
    resolved_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["username"],
                condition=models.Q(status__in=["awaiting_verification", "awaiting_approval"]),
                name="unique_active_registration_username",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(status__in=["awaiting_verification", "awaiting_approval"]),
                name="unique_active_registration_email",
            ),
        ]

    def __str__(self):
        return f"{self.username} ({self.get_status_display()})"
