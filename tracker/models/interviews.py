from django.db import models

from .batches import Group
from .placements import Company, Role
from .users import User


BADGES = {
    "selected": "status-selected",
    "not-selected": "status-not-selected",
    "in-progress": "status-in-progress",
    "pending": "status-pending",
    "cleared": "status-cleared",
    "rejected": "status-rejected",
}


class Interview(models.Model):
    TYPE_CHOICES = [
        ("walk_in", "Walk-in"), ("campus_drive", "Campus drive"),
        ("off_campus", "Off-campus"), ("referral", "Referral"), ("hr_call", "HR call"),
    ]
    ATTENDED, NO_SHOW, RESCHEDULED = "attended", "no_show", "rescheduled"
    ATTENDANCE_CHOICES = [(ATTENDED, "Attended"), (NO_SHOW, "No-show"), (RESCHEDULED, "Rescheduled")]

    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="interviews")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="interviews")
    company_name = models.CharField(max_length=150)
    role = models.CharField(max_length=150)
    company_ref = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="interviews"
    )
    role_ref = models.ForeignKey(
        Role, null=True, blank=True, on_delete=models.SET_NULL, related_name="interviews"
    )
    job_posting_url = models.URLField(blank=True)
    date_of_interview = models.DateField()
    time_of_interview = models.TimeField(blank=True, null=True)
    hr_name = models.CharField(max_length=150, blank=True)
    hr_contact_number = models.CharField(max_length=30, blank=True)
    hr_email = models.EmailField(blank=True)
    prep_notes = models.TextField(blank=True)
    interview_type = models.CharField(max_length=20, choices=TYPE_CHOICES, blank=True)
    attendance = models.CharField(max_length=15, choices=ATTENDANCE_CHOICES, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_of_interview", "-created_at"]

    def save(self, *args, **kwargs):
        self.company_ref = Company.objects.for_name(self.company_name)
        self.role_ref = Role.objects.for_name(self.role)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.company_name} - {self.role}"

    @property
    def final_status(self):
        try:
            return self.status.final_status
        except InterviewStatus.DoesNotExist:
            return "in-progress"

    @property
    def final_label(self):
        return self.final_status.replace("-", " ").capitalize()

    @property
    def badge(self):
        return BADGES[self.final_status]

    @property
    def visible_trainer_notes(self):
        return self.trainer_notes.filter(visible_to_student=True)

    @property
    def calendar_status(self):
        final = self.final_status
        if final == "selected":
            return "selected"
        if final == "not-selected":
            return "rejected"
        return {"attended": "attended", "no_show": "no_show", "rescheduled": "rescheduled"}.get(
            self.attendance, "scheduled")

    @property
    def progress(self):
        rounds = list(self.rounds.all())
        return f"{sum(r.status == 'cleared' for r in rounds)}/{len(rounds)} cleared"


class InterviewNote(models.Model):
    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="trainer_notes")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    text = models.TextField()
    visible_to_student = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class InterviewNoteReply(models.Model):
    note = models.ForeignKey(InterviewNote, on_delete=models.CASCADE, related_name="replies")
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    text = models.TextField()
    seen_by_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]


class InterviewRound(models.Model):
    PENDING, CLEARED, REJECTED = "pending", "cleared", "rejected"
    STATUS_CHOICES = [(PENDING, "Pending"), (CLEARED, "Cleared"), (REJECTED, "Rejected")]

    TYPE_CHOICES = [
        ("aptitude", "Aptitude / online test"), ("coding", "Coding round"),
        ("technical", "Technical interview"), ("group_discussion", "Group discussion"),
        ("managerial", "Managerial round"), ("hr", "HR round"),
        ("assignment", "Assignment"), ("other", "Other"),
    ]

    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="rounds")
    round_type = models.CharField(max_length=20, choices=TYPE_CHOICES, blank=True)
    round_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    scheduled_date = models.DateField(blank=True, null=True)
    scheduled_time = models.TimeField(blank=True, null=True)
    feedback = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["round_number"]
        unique_together = ("interview", "round_number")

    def __str__(self):
        return f"{self.interview} - round {self.round_number}"

    @property
    def badge(self):
        return BADGES[self.status]


class InterviewStatus(models.Model):
    SELECTED, NOT_SELECTED = "selected", "not-selected"
    FINAL_CHOICES = [(SELECTED, "Selected"), (NOT_SELECTED, "Not selected")]

    interview = models.OneToOneField(Interview, on_delete=models.CASCADE, related_name="status")
    final_status = models.CharField(max_length=15, choices=FINAL_CHOICES)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["final_status", "updated_at"])]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.final_status == self.SELECTED:
            from ..services.placements import record_placement

            record_placement(
                self.interview.student, self.interview.company_name, self.interview.group.name
            )

    def __str__(self):
        return f"{self.interview}: {self.final_status}"
