from django.db import migrations, models
from django.utils import timezone


def expire_link_registrations(apps, schema_editor):
    Registration = apps.get_model("tracker", "StudentRegistrationRequest")
    Registration.objects.filter(status="awaiting_verification").update(
        status="expired",
        password_hash="",
        verification_token_hash="",
        resolved_at=timezone.now(),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0005_alter_studentregistrationrequest_status"),
    ]

    operations = [
        migrations.RunPython(expire_link_registrations, migrations.RunPython.noop),
        migrations.RenameField(
            model_name="studentregistrationrequest",
            old_name="verification_token_hash",
            new_name="verification_code_hash",
        ),
        migrations.AddField(
            model_name="studentregistrationrequest",
            name="verification_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
