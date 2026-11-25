from django.conf import settings
from django.db import models
from django.utils.text import slugify

from apps.accounts.models import Role


class Article(models.Model):
    """Documento interno: protocolos, programas, guías. Se escribe en Markdown."""

    title = models.CharField("título", max_length=200)
    slug = models.SlugField(max_length=220, unique=True, editable=False)
    category = models.CharField("categoría", max_length=60, default="General", help_text="Agrupa los documentos en el índice: Incentivos, Protocolos, Herramientas…")
    summary = models.CharField("resumen", max_length=250, blank=True, help_text="Una línea que se ve en el índice.")
    body = models.TextField("contenido", blank=True, help_text="Markdown: # títulos, **negrita**, listas con -, tablas con |.")
    pinned = models.BooleanField("destacado", default=False)
    roles = models.ManyToManyField(Role, verbose_name="solo para estos roles", blank=True, related_name="articles",
                                   help_text="Sin marcar ninguno, lo ve todo el equipo.")
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField("actualizado", auto_now=True)

    class Meta:
        ordering = ["category", "-pinned", "title"]
        verbose_name = "documento"
        verbose_name_plural = "documentos"

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:200] or "documento"
            slug, n = base, 2
            while Article.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @staticmethod
    def visible_to(user):
        """Documentos que esta persona puede leer: los abiertos a todos y los de su rol; quien los gestiona, todos."""
        found = Article.objects.all()
        if user.is_superuser or user.can_manage_knowledge:
            return found
        allowed = models.Q(roles__isnull=True)
        if user.role_id:
            allowed |= models.Q(roles=user.role_id)
        return found.filter(allowed).distinct()
