"""Datos de demostración: un equipo, un catálogo y un pipeline ficticios para probar QopenCRM en un minuto.

Todo es inventado (dominios .example). Pensado para instalaciones de prueba: en una base de datos con
empresas reales no hace nada salvo con --force.
"""
from datetime import timedelta
from decimal import Decimal as D

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.catalog.models import Product, ProductFamily
from apps.crm.models import Activity, Company, Contact, Event, LossReason, Opportunity, OpportunityLine, Stage, Task
from apps.incentives.models import Incentive
from apps.knowledge.models import Article
from apps.sales import services

TEAM = [
    ("julia@demo.example", "Julia Navarro", "responsable-comercial"),
    ("luis@demo.example", "Luis Marín", "comercial"),
    ("ana@demo.example", "Ana Ruiz", "comercial"),
]
FAMILIES = [("Plataforma", "#6372f5"), ("Servicios", "#34c27b"), ("Proyectos", "#f0b445")]
PRODUCTS = [
    # familia, código, nombre, cobro, unidad, precio, precio anual, nivel, extra
    ("Plataforma", "PLAN-LITE", "Plan Lite", "monthly", "mes", "90", "900", 1, False),
    ("Plataforma", "PLAN-BASIC", "Plan Basic", "monthly", "mes", "190", "1900", 2, False),
    ("Plataforma", "PLAN-PLUS", "Plan Plus", "monthly", "mes", "395", "3950", 3, False),
    ("Plataforma", "PLAN-PRO", "Plan Pro", "monthly", "mes", "665", "6650", 4, False),
    ("Plataforma", "USUARIO", "Usuario adicional", "monthly", "usuario/mes", "35", None, None, True),
    ("Servicios", "FORMACION", "Formación", "hourly", "hora", "60", None, None, False),
    ("Servicios", "CONSULTORIA", "Consultoría", "hourly", "hora", "80", None, None, False),
    ("Proyectos", "IMPLANTACION", "Implantación y migración de datos", "one_off", "ud", "2400", None, None, False),
    ("Proyectos", "INTEGRACION", "Integración a medida", "one_off", "ud", None, None, None, False),
]
# empresa, tipo, sector, población, dueño, contacto (nombre, apellidos, cargo), feria
COMPANIES = [
    ("Ondas Rurales", "prospect", "Operador de telecomunicaciones", "Soria", "ana", ("Pablo", "Gil", "Gerente"), None),
    ("TeleSur Comunicaciones", "prospect", "Operador de telecomunicaciones", "Cádiz", "ana", ("Carmen", "Ortega", "Directora de operaciones"), "Feria Conecta 2026"),
    ("Fibranet Levante", "prospect", "Operador de telecomunicaciones", "Castellón", "ana", ("Marta", "Soler", "CTO"), "Feria Conecta 2026"),
    ("Redes del Norte", "prospect", "Ingeniería", "Santander", "luis", ("Iñaki", "Aguirre", "Responsable técnico"), None),
    ("Ayuntamiento de Villanueva", "prospect", "Administración pública", "Villanueva", "luis", ("Rosa", "Campos", "Concejala de innovación"), "Jornada Territorio Digital"),
    ("Cable Atlántico", "prospect", "Operador de telecomunicaciones", "Vigo", "julia", ("Elena", "Rey", "Dirección técnica"), "Feria Conecta 2026"),
    ("Nodo Sistemas", "prospect", "Integrador", "Zaragoza", "ana", ("Sergio", "Lozano", "Director general"), None),
    ("Agro Ribera", "prospect", "Agroalimentario", "Tudela", "luis", ("Nuria", "Vidal", "Responsable de compras"), None),
]
# título, empresa, etapa, líneas [(código o texto libre, cantidad, precio si es libre, cobro)], prioridad, próxima acción (texto, tipo, días)
OPPORTUNITIES = [
    ("Implantación Lite", "Ondas Rurales", "Nuevo lead", [("PLAN-LITE", 1)], 2, None),
    ("Implantación Basic", "TeleSur Comunicaciones", "Nuevo lead", [("PLAN-BASIC", 1)], 2, None),
    ("Implantación Pro", "Fibranet Levante", "Contactado", [("PLAN-PRO", 1), ("CONSULTORIA", 15)], 3, ("Llamar para cerrar fecha de demo", "call", 1)),
    ("Implantación Plus", "Redes del Norte", "Demo / reunión", [("PLAN-PLUS", 1)], 2, ("Enviar resumen de la demo", "email", 3)),
    ("Plataforma municipal", "Ayuntamiento de Villanueva", "Propuesta enviada", [("PLAN-PLUS", 1), ("USUARIO", 1), ("FORMACION", 30), ("IMPLANTACION", 1)], 3, ("Enviar propuesta revisada", "quote", 2)),
    ("Implantación Pro", "Cable Atlántico", "Negociación", [("PLAN-PRO", 1), ("USUARIO", 3), ("FORMACION", 16), ("IMPLANTACION", 1)], 3, ("Llamar para cerrar condiciones", "call", 0)),
    ("Plan Plus + usuarios", "Nodo Sistemas", "Ganada", [("PLAN-PLUS", 1), ("USUARIO", 2), ("FORMACION", 10)], 2, None),
    ("Trazabilidad de campaña", "Agro Ribera", "Perdida", [("PLAN-BASIC", 1)], 1, None),
]
ARTICLES = [
    ("Cómo cualificar un lead", "Ventas", True, "Las cuatro preguntas antes de mover una oportunidad a «Contactado».",
     "## Antes de mover la tarjeta\n\n1. **Necesidad:** ¿qué problema quiere resolver?\n2. **Decisión:** ¿quién firma y quién usa?\n"
     "3. **Plazo:** ¿para cuándo lo necesita?\n4. **Presupuesto:** ¿hay partida este año?\n\n> Si falta alguna, la próxima acción es conseguirla."),
    ("Programa de incentivos", "Equipo", False, "Cómo se calculan y cuándo se pagan los incentivos comerciales.",
     "## Tres programas\n\n| Programa | Cálculo |\n|---|---|\n| Cliente o proyecto nuevo | 5 % del margen, según se cobra |\n"
     "| Cuota recurrente | Media de las cuatro primeras cuotas |\n| Project Manager | 5 % del margen + 1 % plazos + 1 % costes |\n"),
    ("Guion de la demo", "Ventas", False, "Orden recomendado para una demo de 30 minutos.",
     "1. Contexto del cliente (5 min)\n2. Caso de uso principal (15 min)\n3. Preguntas (5 min)\n4. **Siguiente paso con fecha** (5 min)"),
]


class Command(BaseCommand):
    help = "Carga un equipo, un catálogo y un pipeline ficticios para probar la aplicación."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Cargar aunque ya haya empresas en la base de datos")

    @transaction.atomic
    def handle(self, *args, **options):
        if Company.objects.exists() and not options["force"]:
            raise CommandError("Ya hay empresas en la base de datos. Usa --force si de verdad quieres añadir los datos de demostración.")
        call_command("seed", stdout=self.stdout)
        now = timezone.now()
        people = {}
        for email, name, role in TEAM:
            user, _ = User.objects.get_or_create(email=email, defaults={"name": name, "role": Role.objects.get(code=role), "task_emails": False})
            people[email.split("@")[0]] = user
        families = {}
        for order, (name, color) in enumerate(FAMILIES):
            families[name], _ = ProductFamily.objects.get_or_create(name=name, defaults={"order": order, "color": color})
        products = {}
        for family, code, name, billing, unit, price, annual, tier, addon in PRODUCTS:
            products[code], _ = Product.objects.get_or_create(code=code, defaults=dict(
                family=families[family], name=name, billing_model=billing, unit=unit, price=D(price) if price else None,
                price_annual=D(annual) if annual else None, tier_level=tier, is_addon=addon))
        today = timezone.localdate()
        events = {
            "Feria Conecta 2026": Event.objects.get_or_create(name="Feria Conecta 2026", defaults=dict(
                kind="fair", starts_on=today - timedelta(days=40), ends_on=today - timedelta(days=38), place="Madrid"))[0],
            "Jornada Territorio Digital": Event.objects.get_or_create(name="Jornada Territorio Digital", defaults=dict(
                kind="event", starts_on=today - timedelta(days=12), ends_on=today - timedelta(days=12), place="Valladolid"))[0],
        }
        companies, contacts = {}, {}
        for index, (name, kind, sector, city, owner, (first, last, position), event) in enumerate(COMPANIES, start=1):
            slug = name.split()[0].lower()
            company = Company.objects.create(name=name, kind=kind, sector=sector, city=city, owner=people[owner], event=events.get(event),
                                             website=f"www.{slug}.example", email=f"info@{slug}.example")
            companies[name] = company
            contacts[name] = Contact.objects.create(
                company=company, first_name=first, last_name=last, position=position, email=f"{first.lower()}@{slug}.example",
                mobile=f"600 000 {index:03d}", is_primary=True, owner=people[owner], legal_basis="legitimate")
        stages = {stage.name: stage for stage in Stage.objects.all()}
        first_stage = Stage.objects.order_by("order").first()
        for position, (title, company_name, stage_name, lines, priority, action) in enumerate(OPPORTUNITIES):
            company = companies[company_name]
            owner = company.owner
            opp = Opportunity.objects.create(
                title=title, company=company, contact=contacts[company_name], stage=first_stage, owner=owner, priority=priority,
                probability=first_stage.probability, position=position, source="Feria" if company.event_id else "Web",
                expected_close=today + timedelta(days=20 + 10 * position))
            for order, (code, quantity) in enumerate(lines):
                product = products[code]
                OpportunityLine.objects.create(opportunity=opp, product=product, billing_model=product.billing_model, quantity=quantity,
                                               unit=product.unit, unit_price=product.price, order=order)
            opp.recalc()
            Activity.objects.create(company=company, opportunity=opp, contact=contacts[company_name], user=owner, kind="call", direction="out",
                                    occurred_at=now - timedelta(days=6), subject="Primera llamada",
                                    body="Usan hojas de cálculo y quieren centralizar la información. Les interesa ver una demo.")
            stage = stages[stage_name]
            if stage != first_stage:
                if stage.order >= stages["Propuesta enviada"].order and stage.is_open:
                    Activity.objects.create(company=company, opportunity=opp, contact=contacts[company_name], user=owner, kind="email", direction="out",
                                            occurred_at=now - timedelta(days=4), subject="Resumen y siguientes pasos",
                                            body="Enviado dossier y propuesta de demo.")
                    services.create_quote(opp, owner)
                reason = LossReason.objects.filter(name="Precio").first() if stage.is_lost else None
                if opp.move_to(stage, owner, loss_reason=reason, loss_notes="Se quedan con su herramienta actual." if reason else ""):
                    services.fulfil_won(opp, owner)
                    company.kind = "customer"
                    company.save()
            if action:
                text, kind, days = action
                Task.objects.create(title=text, kind=kind, due_at=now + timedelta(days=days, hours=2), assignee=owner, created_by=owner,
                                    company=company, opportunity=opp)
                opp.refresh_next_action()
        # una oportunidad parada a la espera del cliente
        Opportunity.objects.get(title="Implantación Plus").hold(today + timedelta(days=28), "Esperan a cerrar el presupuesto anual.", people["luis"])
        Task.objects.create(title="Preparar la reunión semanal de pipeline", due_at=now - timedelta(days=1), assignee=people["julia"], created_by=people["julia"])
        for title, category, pinned, summary, body in ARTICLES:
            Article.objects.get_or_create(title=title, defaults={"category": category, "pinned": pinned, "summary": summary, "body": body})
        won = Opportunity.objects.get(title="Plan Plus + usuarios")
        Incentive.objects.update_or_create(program="sales_new", user=won.owner, opportunity=won, defaults=dict(
            concept="Nodo Sistemas · Plan Plus", company=won.company, status="approved", revenue=D("6000"), direct_costs=D("1500"), collected=D("3000")))
        self.stdout.write(self.style.SUCCESS(
            "Datos de demostración cargados. Entra con julia@demo.example, luis@demo.example o ana@demo.example: "
            "el código de acceso sale en los registros (./qopencrm.sh logs web) mientras no configures SMTP."))
