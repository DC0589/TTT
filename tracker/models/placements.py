import re

from django.db import models

from .users import User


LEGAL_SUFFIXES = {"pvt", "private", "ltd", "limited", "llp", "inc", "corp", "corporation"}


def normalise_key(name):
    """Case, spacing, punctuation and legal-suffix insensitive key for a name."""
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in LEGAL_SUFFIXES] or words
    return "".join(kept)[:150]


class NameManager(models.Manager):
    def for_name(self, name):
        """Return the shared row for a free-text name, creating it on first use."""
        name = " ".join((name or "").split())[:150]
        key = normalise_key(name)
        if not key:
            return None
        obj, _ = self.get_or_create(key=key, defaults={"name": name})
        return obj


class Company(models.Model):
    name = models.CharField(max_length=150)
    key = models.CharField(max_length=150, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = NameManager()

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "companies"

    def __str__(self):
        return self.name


class Role(models.Model):
    name = models.CharField(max_length=150)
    key = models.CharField(max_length=150, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = NameManager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PlacedStudent(models.Model):
    name = models.CharField(max_length=150)
    batch = models.CharField(max_length=40, blank=True)
    company = models.CharField(max_length=150)
    company_ref = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="placed_students"
    )
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-year", "name"]
        indexes = [models.Index(fields=["company"])]

    def save(self, *args, **kwargs):
        self.company_ref = Company.objects.for_name(self.company)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.company}"


class Selection(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="selections")
    company = models.CharField(max_length=150)
    role = models.CharField(max_length=150, blank=True)
    company_ref = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name="selections"
    )
    role_ref = models.ForeignKey(
        Role, null=True, blank=True, on_delete=models.SET_NULL, related_name="selections"
    )
    added_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="selections_added"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["created_at"])]

    def save(self, *args, **kwargs):
        self.company_ref = Company.objects.for_name(self.company)
        self.role_ref = Role.objects.for_name(self.role)
        super().save(*args, **kwargs)
        from ..services.placements import record_placement

        membership = self.student.memberships.select_related("group").first()
        record_placement(self.student, self.company, membership.group.name if membership else "")

    def __str__(self):
        return f"{self.student} - {self.company}"
