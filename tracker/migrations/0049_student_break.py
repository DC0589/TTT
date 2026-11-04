from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0048_support_chat"),
    ]

    operations = [
        migrations.CreateModel(
            name="StudentBreak",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "admin",
                    models.ForeignKey(
                        limit_choices_to={"is_admin": True},
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="student_breaks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["start_date", "name"],
            },
        ),
    ]
