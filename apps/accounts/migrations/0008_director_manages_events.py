from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.filter(code__in=("director", "responsable-comercial")).update(can_manage_events=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_knowledge_incentive_managers")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
