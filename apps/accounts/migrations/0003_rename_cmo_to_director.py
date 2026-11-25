from django.db import migrations


def forwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    if not Role.objects.filter(code="director").exists():
        Role.objects.filter(code="cmo").update(code="director", name="Director")


def backwards(apps, schema_editor):
    Role = apps.get_model("accounts", "Role")
    if not Role.objects.filter(code="cmo").exists():
        Role.objects.filter(code="director").update(code="cmo", name="CMO")


class Migration(migrations.Migration):
    dependencies = [("accounts", "0002_role_mcp_tools")]
    operations = [migrations.RunPython(forwards, backwards)]
