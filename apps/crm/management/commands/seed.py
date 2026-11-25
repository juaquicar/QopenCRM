"""Datos iniciales: roles, etapas, motivos de pérdida y plantilla de presupuesto. Se puede repetir."""
from django.core.management.base import BaseCommand

from apps.accounts.models import Role, User
from apps.crm.models import LossReason, Stage
from apps.sales.models import QuoteTemplate

ROLES = [
    # código, nombre, ve, edita, borra, exporta, catálogo, etapas
    ("ceo", "CEO", "all", "all", True, True, True, True),
    ("director", "Director", "all", "all", False, True, True, False),
    ("responsable-comercial", "Responsable Comercial", "all", "all", True, True, False, True),
    ("comercial", "Comercial", "own", "own", False, False, False, False),
]
STAGES = [
    ("Nuevo lead", 5, "#8b93a5", False, False), ("Contactado", 15, "#4c8dff", False, False),
    ("Demo / reunión", 35, "#7cc4f8", False, False), ("Propuesta enviada", 60, "#f0b445", False, False),
    ("Negociación", 80, "#6372f5", False, False), ("Ganada", 100, "#34c27b", True, False), ("Perdida", 0, "#f06464", False, True),
]
REASONS = ["Precio", "Eligió a la competencia", "Sin presupuesto", "Sin respuesta", "No encaja el producto", "Proyecto aplazado"]
TERMS = ("Forma de pago: transferencia bancaria a 30 días desde la fecha de factura.\n"
         "Las cuotas recurrentes se facturan por adelantado según el periodo indicado.\n"
         "Los servicios por horas se facturan según las horas realmente consumidas.")


class Command(BaseCommand):
    help = "Crea roles, etapas, motivos de pérdida y plantilla por defecto. Con --admin crea el primer superusuario."

    def add_arguments(self, parser):
        parser.add_argument("--admin", help="Correo del superusuario inicial")
        parser.add_argument("--name", default="", help="Nombre del superusuario inicial")

    def handle(self, *args, **options):
        for order, (code, name, view, edit, delete, export, catalog, pipeline) in enumerate(ROLES):
            Role.objects.get_or_create(code=code, defaults=dict(
                name=name, order=order, view_scope=view, edit_scope=edit, can_delete=delete, can_export=export,
                can_manage_catalog=catalog, can_manage_pipeline=pipeline, can_manage_events=code == "responsable-comercial",
                can_manage_knowledge=code in ("ceo", "director"), can_manage_incentives=code in ("ceo", "director", "responsable-comercial")))
        if not Stage.objects.exists():
            for order, (name, prob, color, won, lost) in enumerate(STAGES):
                Stage.objects.create(name=name, order=order, probability=prob, color=color, is_won=won, is_lost=lost)
        for order, name in enumerate(REASONS):
            LossReason.objects.get_or_create(name=name, defaults={"order": order})
        if not QuoteTemplate.objects.exists():
            QuoteTemplate.objects.create(
                name="Propuesta estándar", is_default=True, terms=TERMS,
                intro="Estimado/a {{contacto}}:\n\nGracias por su interés. A continuación detallamos nuestra propuesta para {{cuenta}}.")
        email = options.get("admin")
        if email:
            user, created = User.objects.get_or_create(email=email.lower(), defaults={"name": options["name"], "is_superuser": True, "is_staff": True})
            if created:
                user.set_unusable_password()
                user.save()
            elif not user.is_superuser:
                user.is_superuser = user.is_staff = True
                user.save()
            self.stdout.write(self.style.SUCCESS(f"Superusuario listo: {user.email}"))
        self.stdout.write(self.style.SUCCESS("Datos iniciales listos."))
