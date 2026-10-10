import re

from django.db import migrations

LEGAL_SUFFIXES = {"pvt", "private", "ltd", "limited", "llp", "inc", "corp", "corporation"}


def normalise_key(name):
    words = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).split()
    kept = [w for w in words if w not in LEGAL_SUFFIXES] or words
    return "".join(kept)[:150]


def link(apps, model_name, field, target_name, ref_field):
    Model = apps.get_model("tracker", model_name)
    Target = apps.get_model("tracker", target_name)
    cache = {}
    for obj in Model.objects.filter(**{f"{ref_field}__isnull": True}).only("pk", field):
        name = " ".join((getattr(obj, field) or "").split())[:150]
        key = normalise_key(name)
        if not key:
            continue
        if key not in cache:
            cache[key], _ = Target.objects.get_or_create(key=key, defaults={"name": name})
        Model.objects.filter(pk=obj.pk).update(**{ref_field: cache[key]})


def backfill(apps, schema_editor):
    link(apps, "PlacedStudent", "company", "Company", "company_ref")
    link(apps, "Selection", "company", "Company", "company_ref")
    link(apps, "Selection", "role", "Role", "role_ref")
    link(apps, "Interview", "company_name", "Company", "company_ref")
    link(apps, "Interview", "role", "Role", "role_ref")


class Migration(migrations.Migration):
    dependencies = [("tracker", "0036_company_role")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
