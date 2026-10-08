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
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="interviews")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="interviews")
    company_name = models.CharField(max_length=150)
    role = models.CharField(max_length=150)
    job_posting_url = models.URLField(blank=True)
    date_of_interview = models.DateField()
    hr_name = models.CharField(max_length=150, blank=True)
    hr_contact_number = models.CharField(max_length=30, blank=True)
    hr_email = models.EmailField(blank=True)
    prep_notes = models.TextField(blank=True)
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
    def progress(self):
        rounds = list(self.rounds.all())
        return f"{sum(r.status == 'cleared' for r in rounds)}/{len(rounds)} cleared"


class InterviewRound(models.Model):
    PENDING, CLEARED, REJECTED = "pending", "cleared", "rejected"
    STATUS_CHOICES = [(PENDING, "Pending"), (CLEARED, "Cleared"), (REJECTED, "Rejected")]

    interview = models.ForeignKey(Interview, on_delete=models.CASCADE, related_name="rounds")
    round_number = models.PositiveIntegerField()
    description = models.CharField(max_length=255)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    scheduled_date = models.DateField(blank=True, null=True)
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

    INTEGRITY_PENALTIES = {
        "tab_hidden": 10, "window_blur": 5, "paste": 10, "copy": 3,
        "context_menu": 2, "devtools_key": 5, "auto_ended": 0,
    }

    @property
    def integrity_flag_count(self):
        return sum(1 for e in (self.integrity_events or []) if e.get("type") != "auto_ended")

    @property
    def integrity_score(self):
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
        return any(e.get("type") == "auto_ended" for e in (self.integrity_events or []))

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
