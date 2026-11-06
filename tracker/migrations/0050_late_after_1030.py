import datetime

from django.db import migrations, models


def move_cutoff(apps, schema_editor):
    AttendanceSettings = apps.get_model("tracker", "AttendanceSettings")
    AttendanceSettings.objects.filter(late_after=datetime.time(10, 0)).update(late_after=datetime.time(10, 30))


class Migration(migrations.Migration):

    dependencies = [("tracker", "0049_student_break")]

    operations = [
        migrations.AlterField(
            model_name="attendancesettings",
            name="late_after",
            field=models.TimeField(default=datetime.time(10, 30)),
        ),
        migrations.RunPython(move_cutoff, migrations.RunPython.noop),
    ]
