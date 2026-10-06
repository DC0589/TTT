from datetime import date, timedelta

from django.core.management.base import BaseCommand

from tracker.models import (
    Group,
    GroupMembership,
    Interview,
    InterviewRound,
    InterviewStatus,
    User,
)

PASSWORD = "demo12345"


class Command(BaseCommand):
    help = "Create demo data: 1 admin, 3 students, 1 batch, 2 interviews."

    def handle(self, *args, **opts):
        # 1. Create Admin User
        admin, admin_created = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@example.com", "is_admin": True},
        )
        if admin_created:
            admin.set_password(PASSWORD)
            admin.save()

        # 2. Create Student Users
        students = []
        for name in ("alice", "bob", "carol"):
            u, created = User.objects.get_or_create(
                username=name,
                defaults={"email": f"{name}@example.com", "is_student": True},
            )
            if created:
                u.set_password(PASSWORD)
                u.save()
            students.append(u)

        # 3. Create Group & Memberships
        group, _ = Group.objects.get_or_create(
            name="Batch 2026",
            admin=admin,
            defaults={"description": "Final-year placement batch"},
        )
        for s in students:
            GroupMembership.objects.get_or_create(group=group, student=s)

        # 4. Define Interview Seed Specifications (including required HR contact info)
        today = date.today()
        specs = [
            (
                students[0],
                "Acme Corp",
                "Backend Engineer",
                today - timedelta(days=10),
                "Jane Doe",
                "hr@acme.com",
                "+1234567890",
                "selected",
                [
                    ("Online coding test", "cleared"),
                    ("Technical interview", "cleared"),
                    ("HR round", "cleared"),
                ],
            ),
            (
                students[1],
                "Globex",
                "Data Analyst",
                today - timedelta(days=3),
                "John Smith",
                "hr@globex.com",
                "+0987654321",
                None,
                [
                    ("Aptitude test", "cleared"),
                    ("Technical interview", "pending"),
                ],
            ),
        ]

        # 5. Seed Interviews, Rounds, and Statuses
        for (
            student,
            company,
            role,
            day,
            hr_name,
            hr_email,
            hr_phone,
            final,
            rounds,
        ) in specs:
            iv, created = Interview.objects.get_or_create(
                student=student,
                group=group,
                company_name=company,
                defaults={
                    "role": role,
                    "date_of_interview": day,
                    "hr_name": hr_name,
                    "hr_email": hr_email,
                    "hr_contact_number": hr_phone,
                },
            )
            if not created:
                continue

            for i, (desc, st) in enumerate(rounds, 1):
                InterviewRound.objects.create(
                    interview=iv,
                    round_number=i,
                    description=desc,
                    status=st,
                )

            if final:
                InterviewStatus.objects.create(interview=iv, final_status=final)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded demo data. New demo accounts use password '{PASSWORD}'; "
                "existing account passwords were left unchanged."
            )
        )