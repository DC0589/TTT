from django.db import models
from django.utils import timezone

from .users import User


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
