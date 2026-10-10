from django.db import migrations
from django.utils import timezone


def backfill(apps, schema_editor):
    PlacedStudent = apps.get_model("tracker", "PlacedStudent")
    InterviewStatus = apps.get_model("tracker", "InterviewStatus")
    Selection = apps.get_model("tracker", "Selection")
    year = timezone.localdate().year
    seen = {(n.lower(), c.lower()) for n, c in PlacedStudent.objects.values_list("name", "company")}

    def add(student, company, batch):
        company = " ".join((company or "").split())
        name = (f"{student.first_name} {student.last_name}".strip() or student.username)[:150]
        if company and (name.lower(), company.lower()) not in seen:
            seen.add((name.lower(), company.lower()))
            PlacedStudent.objects.create(name=name, company=company[:150], batch=batch[:40], year=year)

    for st in InterviewStatus.objects.filter(final_status="selected").select_related(
        "interview__student", "interview__group"
    ):
        add(st.interview.student, st.interview.company_name, st.interview.group.name)
    for sel in Selection.objects.select_related("student"):
        add(sel.student, sel.company, "")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0032_selection")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
