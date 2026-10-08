from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracker", "0014_studentregistrationrequest_verification_last_sent_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="referred_by",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="user",
            name="mobile_number",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="user",
            name="graduation",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="user",
            name="department",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="user",
            name="hometown",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="user",
            name="parent_name",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="user",
            name="parent_mobile_number",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="user",
            name="skills",
            field=models.TextField(blank=True),
        ),
    ]
