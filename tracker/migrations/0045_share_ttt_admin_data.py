from django.db import migrations


def link(apps, schema_editor):
    User = apps.get_model("tracker", "User")
    primary = User.objects.filter(username="admin", is_admin=True).first()
    partner = User.objects.filter(username="TTT_admin", is_admin=True).first()
    if primary and partner and primary.pk != partner.pk:
        partner.shares_data_with = primary
        partner.save(update_fields=["shares_data_with"])


class Migration(migrations.Migration):
    dependencies = [("tracker", "0044_admin_data_sharing")]
    operations = [migrations.RunPython(link, migrations.RunPython.noop)]
