from django.db import models
from django.utils import timezone

from .users import User


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
    end_reason = models.CharField(max_length=300, blank=True)
    question_set = models.ForeignKey(
        "MockQuestionSet", on_delete=models.SET_NULL, blank=True, null=True,
        related_name="sessions",
    )
    integrity_summary = models.JSONField(default=dict, blank=True)
    events_purged_at = models.DateTimeField(blank=True, null=True)

    INTEGRITY_PENALTIES = {
        "tab_hidden": 10, "window_blur": 5, "paste": 10, "copy": 3,
        "context_menu": 2, "devtools_key": 5, "screen_share_stopped": 10, "auto_ended": 0,
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
    # Raw answer sent to the AI; cleared once scored so media does not fill the database.
    payload = models.JSONField(blank=True, null=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(blank=True, null=True)
    last_attempt_at = models.DateTimeField(blank=True, null=True)
    claimed_at = models.DateTimeField(blank=True, null=True)

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


class MockQuestionSet(models.Model):
    """A ready-made set of interview questions served without calling the AI."""
    topic = models.CharField(max_length=120)
    difficulty = models.CharField(max_length=10, default="medium")
    questions = models.JSONField(default=list)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["topic", "difficulty", "-id"]
        indexes = [models.Index(fields=["topic", "difficulty", "is_active"])]

    def __str__(self):
        return f"{self.topic} [{self.difficulty}] set {self.pk}"


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
