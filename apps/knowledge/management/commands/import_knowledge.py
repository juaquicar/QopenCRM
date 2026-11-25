"""Carga documentos en el Banco de conocimiento desde una carpeta con index.json y ficheros Markdown.

index.json: lista de {file, title, category, summary?, pinned?}. Se puede repetir: cada documento se reconoce por
su fichero de origen y, si alguien lo ha editado ya en el CRM, no se pisa salvo con --overwrite.
"""
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.knowledge.models import Article


class Command(BaseCommand):
    help = "Importa documentos Markdown al Banco de conocimiento."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--overwrite", action="store_true", help="Sustituir el contenido de los que ya existen")

    def handle(self, *args, **options):
        folder = Path(options["folder"])
        created = updated = kept = 0
        for item in json.loads((folder / "index.json").read_text(encoding="utf-8")):
            body = (folder / item["file"]).read_text(encoding="utf-8")
            key = f"import:{item['file']}"
            article = Article.objects.filter(external_id=key).first()
            if article and not options["overwrite"]:
                kept += 1
                continue
            article = article or Article(external_id=key)
            created, updated = created + (article.pk is None), updated + (article.pk is not None)
            article.title, article.category, article.body = item["title"], item["category"], body
            article.summary, article.pinned = item.get("summary", ""), item.get("pinned", False)
            article.save()
        self.stdout.write(self.style.SUCCESS(f"{created} documentos creados, {updated} actualizados, {kept} sin tocar."))
