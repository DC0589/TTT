from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


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


class Group(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    admin = models.ForeignKey(User, on_delete=models.CASCADE, related_name="groups_created",
                              limit_choices_to={"is_admin": True})
    students = models.ManyToManyField(User, through="GroupMembership", related_name="student_groups")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


class GroupMembership(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships",
                                limit_choices_to={"is_student": True})
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("group", "student")

    def __str__(self):
        return f"{self.student} in {self.group}"


class LearningCourse(models.Model):
    name = models.CharField(max_length=100, unique=True)
    summary = models.TextField()
    level = models.CharField(max_length=40, default="Beginner")
    duration = models.CharField(max_length=60, blank=True)
    prerequisites = models.TextField(blank=True)
    learning_outcomes = models.TextField(
        blank=True,
        help_text="Add one learning outcome per line.",
    )
    tools = models.TextField(
        blank=True,
        help_text="Add one tool or platform per line.",
    )
    roadmap = models.TextField(help_text="Add one roadmap step per line.")
    interview_questions = models.TextField(
        help_text="Add one sample interview question per line."
    )
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


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


def record_placement(student, company, batch=""):
    company = " ".join((company or "").split())
    if not company:
        return
    name = student.get_full_name() or student.username
    if PlacedStudent.objects.filter(name__iexact=name, company__iexact=company).exists():
        return
    PlacedStudent.objects.create(
        name=name[:150], company=company[:150], batch=(batch or "")[:40],
        year=timezone.localdate().year,
    )


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
            record_placement(
                self.interview.student, self.interview.company_name, self.interview.group.name
            )

    def __str__(self):
        return f"{self.interview}: {self.final_status}"


class MockInterviewSession(models.Model):
    student = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="mock_interview_sessions"
    )
    role = models.CharField(max_length=120)
    rating = models.DecimalField(max_digits=3, decimal_places=2, blank=True, null=True)
    expected_answers = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)
    integrity_events = models.JSONField(default=list, blank=True)
    difficulty = models.CharField(max_length=10, blank=True)
    integrity_summary = models.JSONField(default=dict, blank=True)
    events_purged_at = models.DateTimeField(blank=True, null=True)

    INTEGRITY_PENALTIES = {
        "tab_hidden": 10, "window_blur": 5, "paste": 10, "copy": 3,
        "context_menu": 2, "devtools_key": 5, "auto_ended": 0,
    }

    @property
    def integrity_flag_count(self):
        if self.events_purged_at:
            return self.integrity_summary.get("flags", 0)
        return sum(1 for e in (self.integrity_events or []) if e.get("type") != "auto_ended")

    @property
    def integrity_score(self):
        if self.events_purged_at:
            return self.integrity_summary.get("score", 100)
        penalty = sum(self.INTEGRITY_PENALTIES.get(e.get("type"), 0) for e in (self.integrity_events or []))
        return max(0, 100 - penalty)

    @property
    def integrity_level(self):
        score = self.integrity_score
        if score >= 80:
            return "low"
        return "medium" if score >= 50 else "high"

    @property
    def auto_ended(self):
        if self.events_purged_at:
            return bool(self.integrity_summary.get("auto_ended"))
        return any(e.get("type") == "auto_ended" for e in (self.integrity_events or []))

    def purge_integrity_events(self):
        """Drop the detailed flag log but keep the score summary."""
        self.integrity_summary = {
            "score": self.integrity_score, "flags": self.integrity_flag_count,
            "auto_ended": self.auto_ended,
        }
        self.integrity_events = []
        self.events_purged_at = timezone.now()
        self.save(update_fields=["integrity_summary", "integrity_events", "events_purged_at"])

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.student} - {self.role}"


class MockInterviewScore(models.Model):
    PENDING, COMPLETE, FAILED = "pending", "complete", "failed"
    STATUS_CHOICES = [(PENDING, "Pending"), (COMPLETE, "Complete"), (FAILED, "Failed")]

    session = models.ForeignKey(
        MockInterviewSession, on_delete=models.CASCADE, related_name="scores"
    )
    question_number = models.PositiveSmallIntegerField()
    question = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    score = models.PositiveSmallIntegerField(blank=True, null=True)
    answer_transcript = models.TextField(blank=True)
    answer_feedback = models.TextField(blank=True)
    camera_feedback = models.TextField(blank=True)
    screen_feedback = models.TextField(blank=True)

    class Meta:
        ordering = ["question_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "question_number"],
                name="unique_mock_interview_question_score",
            ),
        ]

    def __str__(self):
        return f"{self.session} - question {self.question_number}: {self.score}/5"


class MockQuestion(models.Model):
    EASY, MEDIUM, HARD = "easy", "medium", "hard"
    DIFFICULTY_CHOICES = [(EASY, "Easy"), (MEDIUM, "Medium"), (HARD, "Hard")]
    CONCEPT, CODING = "concept", "coding"
    KIND_CHOICES = [(CONCEPT, "Concept (spoken)"), (CODING, "Coding")]
    LANGUAGE_CHOICES = [("", "Topic default"), ("python", "Python"), ("sql", "SQL"), ("pyspark", "PySpark")]

    topic = models.CharField(max_length=120)
    difficulty = models.CharField(max_length=10, choices=DIFFICULTY_CHOICES, default=MEDIUM)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=CONCEPT)
    language = models.CharField(max_length=10, choices=LANGUAGE_CHOICES, blank=True)
    text = models.TextField()
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["topic", "difficulty", "id"]

    def __str__(self):
        return f"{self.topic} [{self.difficulty}] {self.text[:50]}"


class PlacedStudent(models.Model):
    name = models.CharField(max_length=150)
    batch = models.CharField(max_length=40, blank=True)
    company = models.CharField(max_length=150)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "name"]
        indexes = [models.Index(fields=["company"])]

    def __str__(self):
        return f"{self.name} - {self.company}"


class Selection(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="selections")
    company = models.CharField(max_length=150)
    role = models.CharField(max_length=150, blank=True)
    added_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="selections_added"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_at"])]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        membership = self.student.memberships.select_related("group").first()
        record_placement(self.student, self.company, membership.group.name if membership else "")

    def __str__(self):
        return f"{self.student} - {self.company}"


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


def _invalidate_banner_cache(**kwargs):
    from django.core.cache import cache
    from .context_processors import BANNER_CACHE_KEY
    cache.delete(BANNER_CACHE_KEY)


for _model in (PlacedStudent, Selection, InterviewStatus):
    models.signals.post_save.connect(_invalidate_banner_cache, sender=_model, weak=False)
    models.signals.post_delete.connect(_invalidate_banner_cache, sender=_model, weak=False)
