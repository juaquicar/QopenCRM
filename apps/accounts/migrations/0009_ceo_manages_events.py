from django.db import migrations


def forwards(apps, schema_editor):
    apps.get_model("accounts", "Role").objects.filter(code="ceo").update(can_manage_events=True)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0008_director_manages_events")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
