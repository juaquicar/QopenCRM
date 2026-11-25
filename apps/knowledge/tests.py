from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Role, User

from .models import Article
from .render import to_html


class KnowledgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)
        cls.ana = User.objects.create_user("ana@example.com", name="Ana", role=Role.objects.get(code="comercial"))
        cls.dir = User.objects.create_user("dir@example.com", name="Dir", role=Role.objects.get(code="director"))
        cls.public = Article.objects.create(title="Wifi de la oficina", category="Oficina", body="- **SSID:** OFICINA")
        cls.private = Article.objects.create(title="Infraestructura", category="Herramientas", body="| IP |\n|---|\n| 10.0.0.1 |")
        cls.private.roles.add(Role.objects.get(code="director"))

    def test_markdown_is_rendered_and_sanitised(self):
        html = to_html("# Hola\n\n<script>alert(1)</script>\n\n[x](javascript:alert(1)) **negrita**\n\n| a |\n|---|\n| b |")
        self.assertIn("<h1>Hola</h1>", html)
        self.assertIn("<strong>negrita</strong>", html)
        self.assertIn("<table>", html)
        self.assertNotIn("<script", html)
        self.assertNotIn("javascript:", html)

    def test_reading_respects_role_restrictions(self):
        self.client.force_login(self.ana)
        page = self.client.get("/conocimiento/")
        self.assertContains(page, "Wifi de la oficina")
        self.assertNotContains(page, "Infraestructura")
        self.assertEqual(self.client.get(f"/conocimiento/{self.private.slug}/").status_code, 404)
        self.assertContains(self.client.get(f"/conocimiento/{self.public.slug}/"), "<strong>SSID:</strong>")
        self.assertContains(self.client.get("/conocimiento/?q=oficina"), "Wifi de la oficina")
        self.assertNotContains(self.client.get("/conocimiento/?q=10.0.0.1"), "Infraestructura")
        self.client.force_login(self.dir)
        self.assertContains(self.client.get("/conocimiento/"), "Infraestructura")

    def test_only_managers_write(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get("/conocimiento/nuevo/").status_code, 403)
        self.assertEqual(self.client.post(f"/conocimiento/{self.public.slug}/borrar/").status_code, 403)
        self.assertNotContains(self.client.get(f"/conocimiento/{self.public.slug}/"), "Editar")
        self.client.force_login(self.dir)
        response = self.client.post("/conocimiento/nuevo/", {"title": "Protocolo de Desconexión Digital", "category": "Protocolos", "summary": "", "body": "Texto"})
        article = Article.objects.get(title="Protocolo de Desconexión Digital")
        self.assertRedirects(response, f"/conocimiento/{article.slug}/")
        self.assertEqual((article.slug, article.updated_by), ("protocolo-de-desconexion-digital", self.dir))
        self.client.post(f"/conocimiento/{article.slug}/editar/", {"title": "Protocolo de Desconexión Digital", "category": "Protocolos", "body": "Nuevo"})
        article.refresh_from_db()
        self.assertEqual(article.body, "Nuevo")
        self.client.post(f"/conocimiento/{article.slug}/borrar/")
        self.assertFalse(Article.objects.filter(pk=article.pk).exists())

    def test_import_is_repeatable_and_keeps_edits(self):
        import json
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp())
        (folder / "a.md").write_text("Contenido original")
        (folder / "index.json").write_text(json.dumps([{"file": "a.md", "title": "Formación", "category": "Personas", "summary": "Udemy"}]))
        call_command("import_knowledge", str(folder), verbosity=0)
        article = Article.objects.get(title="Formación")
        article.body = "Editado en el CRM"
        article.save()
        call_command("import_knowledge", str(folder), verbosity=0)
        article.refresh_from_db()
        self.assertEqual((article.body, Article.objects.filter(title="Formación").count()), ("Editado en el CRM", 1))
        call_command("import_knowledge", str(folder), "--overwrite", verbosity=0)
        article.refresh_from_db()
        self.assertEqual(article.body, "Contenido original")
