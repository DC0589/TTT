from django.db import models

from .users import User


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
