from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    Role.objects.filter(code__in=["ceo", "director"]).update(can_manage_knowledge=True)
    Role.objects.filter(code__in=["ceo", "director", "responsable-comercial"]).update(can_manage_incentives=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0006_role_knowledge_incentives")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
