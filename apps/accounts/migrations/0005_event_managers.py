from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.filter(code="responsable-comercial").update(can_manage_events=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_role_can_manage_events")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
