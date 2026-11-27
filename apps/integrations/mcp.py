"""Servidor MCP de QopenCRM: el CRM como herramientas para un asistente de IA.

Cada persona se conecta con su clave de API personal y el asistente actúa con sus permisos, ni más ni menos:
ve lo que ella ve y solo puede cambiar lo que ella puede cambiar. Sobre eso, el superusuario puede recortar por
rol qué herramientas se ofrecen (Ajustes → Roles).

Transporte: HTTP con JSON-RPC 2.0 en un único punto de entrada (POST /mcp/), sin estado y sin SSE.
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_not_required
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt

from apps.accounts.models import AccessLog, User, log_access
from apps.accounts.permissions import scope
from apps.catalog.models import BillingModel, Product, ProductFamily
from apps.crm import history, mentions, reports
from apps.crm.filters import OpportunityFilter, TaskFilter
from apps.crm.models import Activity, Company, Contact, Event, LossReason, Opportunity, OpportunityLine, Stage, Task, normalize_name
from apps.sales import services
from apps.sales.models import HourBank, HourEntry, Quote, QuoteTemplate, Subscription

from .models import ApiKey, hash_key
from .webhooks import emit

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "QopenCRM", "version": "1.0.0"}
INSTRUCTIONS = (
    "CRM comercial de código abierto. Actúas con los permisos de la persona conectada. "
    "Una «empresa» es el cliente o posible cliente; un «contacto», una persona suya; una «oportunidad» (lead), una venta en curso. "
    "Los importes van siempre separados en recurrente mensual (MRR), servicios por horas y pago único: no los sumes en una cifra. "
    "Toda oportunidad abierta debe tener una próxima acción con fecha: si creas o tocas una y no la tiene, propónla. "
    "Antes de crear una empresa o un contacto, búscalo para no duplicar. El CRM no escribe a clientes salvo al enviar un presupuesto "
    "(send_quote): confirma siempre con la persona antes de usarla. Fechas en ISO 8601, hora de Madrid."
)
MAX_ROWS = 50


class ToolError(Exception):
    """Fallo que se devuelve al asistente como resultado de la herramienta, con un mensaje que pueda corregir."""


TOOLS = {}


def tool(name, description, properties=None, required=(), needs=None, write=False, destructive=False):
    """Registra una herramienta. `needs` es el permiso del rol que exige, además del alcance por registro."""

    def register(handler):
        TOOLS[name] = {
            "name": name, "description": description, "handler": handler, "needs": needs, "write": write, "destructive": destructive,
            "schema": {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False},
        }
        return handler

    return register


NEEDS = {
    "catalog": lambda user: user.can_manage_catalog,
    "delete": lambda user: user.can_delete,
    "reports": lambda user: user.can_see_reports,
    "import": lambda user: user.edits_all,
    "events": lambda user: user.can_manage_events,
}


def tools_for(user):
    """Herramientas que se ofrecen a esta persona: las que su rol permite, recortadas por la lista del rol si la hay."""
    if not user.can_use_api:
        return []
    allowed = set(user.role.mcp_tools) if not user.is_superuser and user.role_id and user.role.mcp_tools else None
    return [spec for spec in TOOLS.values()
            if (spec["needs"] is None or NEEDS[spec["needs"]](user)) and (allowed is None or spec["name"] in allowed)]


def tool_choices():
    return [(spec["name"], f"{spec['name']} — {spec['description'].split('.')[0]}") for spec in TOOLS.values()]


# ------------------------------------------------------------------ utilidades
S, I, N, B = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}


def desc(schema, text, **extra):
    return {**schema, "description": text, **extra}


def money(value):
    return None if value is None else float(value)


def when(value):
    return timezone.localtime(value).isoformat(timespec="minutes") if value else None


def limit_of(args):
    return max(1, min(int(args.get("limit") or 20), MAX_ROWS))


def fetch(model, user, pk, label):
    obj = scope(model.objects.all(), user).filter(pk=pk).first() if str(pk).lstrip("-").isdigit() else None
    if obj is None:
        raise ToolError(f"No existe {label} con id {pk}, o no tienes acceso.")
    return obj


def editable(user, obj):
    if not user.can_edit(obj):
        raise ToolError("Tu rol no te permite modificar este registro: no eres su responsable ni estás asignado.")
    return obj


def to_datetime(value, field="fecha"):
    """Fecha y hora en ISO 8601, o atajos: hoy, mañana, +3d, +2w."""
    text = str(value or "").strip().lower()
    now = timezone.localtime()
    morning = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if text in ("hoy", "today"):
        return max(now + timedelta(hours=1), morning)
    if text in ("mañana", "manana", "tomorrow"):
        return morning + timedelta(days=1)
    if text.startswith("+") and text[-1:] in ("d", "w") and text[1:-1].isdigit():
        return morning + timedelta(days=int(text[1:-1]) * (7 if text.endswith("w") else 1))
    parsed = parse_datetime(text.upper()) if "t" in text or ":" in text else None
    if parsed is None and parse_date(text):
        parsed = datetime.combine(parse_date(text), morning.time())
    if parsed is None:
        raise ToolError(f"No entiendo la {field} «{value}». Usa ISO 8601 (2026-12-01T10:00), una fecha (2026-12-01) o: hoy, mañana, +3d, +2w.")
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def to_date(value, field="fecha"):
    parsed = parse_date(str(value or ""))
    if parsed is None:
        raise ToolError(f"No entiendo la {field} «{value}». Usa AAAA-MM-DD.")
    return parsed


def to_decimal(value, field):
    try:
        return services.to_decimal(value)
    except ValueError as exc:
        raise ToolError(f"{field}: {exc}")


def find_user(value, field="persona"):
    """Persona del equipo por id, correo o nombre."""
    text = str(value).strip()
    people = User.objects.filter(is_active=True)
    found = people.filter(pk=text) if text.isdigit() else people.filter(Q(email__iexact=text) | Q(name__iexact=text))
    if not found.exists() and not text.isdigit():
        found = people.filter(name__icontains=text)
    if found.count() != 1:
        raise ToolError(f"No encuentro una única {field} para «{value}». Usa list_team para ver ids y correos.")
    return found.first()


def find_stage(value):
    text = str(value).strip()
    stage = Stage.objects.filter(pk=text).first() if text.isdigit() else Stage.objects.filter(name__iexact=text).first()
    if stage is None:
        raise ToolError(f"No existe la etapa «{value}». Etapas: {', '.join(Stage.objects.values_list('name', flat=True))}.")
    return stage


def find_event(value):
    text = str(value).strip()
    event = Event.objects.filter(pk=text).first() if text.isdigit() else (Event.objects.filter(name__iexact=text).first() or Event.objects.filter(name__icontains=text).first())
    if event is None:
        raise ToolError(f"No existe la feria o evento «{value}». Usa list_events o créalo con create_event.")
    return event


def apply(obj, args, fields):
    """Copia a `obj` los campos de texto que vengan en `args`. Devuelve los que han cambiado."""
    changed = []
    for name in fields:
        if name in args and args[name] is not None:
            setattr(obj, name, args[name])
            changed.append(name)
    return changed


# ---------------------------------------------------------------- serializado
def company_row(company):
    return {"id": company.pk, "name": company.name, "kind": company.get_kind_display(), "city": company.city, "tax_id": company.tax_id,
            "owner": str(company.owner) if company.owner_id else None, "met_at": str(company.event) if company.event_id else None}


def contact_row(contact):
    return {"id": contact.pk, "name": contact.full_name, "position": contact.position, "email": contact.email, "phone": contact.phone,
            "mobile": contact.mobile, "company_id": contact.company_id, "company": contact.company.name if contact.company_id else None,
            "other_companies": [{"id": c.pk, "name": c.name} for c in contact.other_companies.all()],
            "emails": contact.values("email"), "phones": contact.values("phone"),
            "legal_basis": contact.get_legal_basis_display(), "erased": bool(contact.erased_at)}


def opportunity_row(opp):
    return {"id": opp.pk, "title": opp.title, "company_id": opp.company_id, "company": opp.company.name, "stage": opp.stage.name,
            "open": opp.is_open, "owner": str(opp.owner) if opp.owner_id else None, "priority": opp.get_priority_display(),
            "probability": opp.probability, "expected_close": opp.expected_close.isoformat() if opp.expected_close else None,
            "mrr": money(opp.amount_mrr), "hourly_services": money(opp.amount_services), "one_off": money(opp.amount_one_off),
            "next_action_at": when(opp.next_action_at), "source": opp.source,
            "on_hold_until": opp.on_hold_until.isoformat() if opp.on_hold else None, "on_hold_reason": opp.on_hold_reason if opp.on_hold else None}


def task_row(task):
    return {"id": task.pk, "title": task.title, "kind": task.get_kind_display(), "due_at": when(task.due_at), "done": bool(task.done_at),
            "assignee": str(task.assignee), "also": [str(u) for u in task.assignees.all()], "opportunity_id": task.opportunity_id, "company_id": task.company_id, "notes": task.notes}


def activity_row(activity):
    return {"id": activity.pk, "kind": activity.get_kind_display(), "direction": activity.get_direction_display() if activity.direction else None,
            "at": when(activity.occurred_at), "user": str(activity.user) if activity.user_id else None, "subject": activity.subject,
            "body": activity.body[:1500], "opportunity_id": activity.opportunity_id, "company_id": activity.company_id}


def line_row(line):
    return {"id": line.pk, "product": line.product.code if line.product_id else None, "description": line.description,
            "billing_model": line.billing_model, "period": line.period if line.billing_model == BillingModel.MONTHLY else None,
            "quantity": money(line.quantity), "unit": line.unit, "unit_price": money(line.unit_price), "discount_pct": money(line.discount),
            "total": money(line.total)}


def quote_row(quote):
    return {"id": quote.pk, "number": quote.number, "version": quote.version, "title": quote.title, "status": quote.get_status_display(),
            "issued_on": quote.issued_on.isoformat(), "valid_until": quote.valid_until.isoformat() if quote.valid_until else None,
            "base": money(quote.base), "tax": money(quote.tax), "total": money(quote.total), "breakdown": quote.breakdown,
            "opportunity_id": quote.opportunity_id, "company": quote.opportunity.company.name, "sent_at": when(quote.sent_at),
            "is_latest": quote.is_latest}


def product_row(product):
    return {"id": product.pk, "code": product.code, "name": product.name, "family": str(product.family) if product.family_id else None,
            "billing_model": product.billing_model, "unit": product.unit, "price": money(product.price),
            "price_annual": money(product.price_annual), "status": product.status, "is_addon": product.is_addon}


# ------------------------------------------------------------------- lectura
@tool("whoami", "Quién está conectado, su rol y qué puede hacer.")
def whoami(user, args):
    return {"name": str(user), "email": user.email, "role": user.role_name, "sees": "todo el equipo" if user.sees_all else "solo lo suyo",
            "edits": "todo" if user.edits_all else "solo lo suyo", "can_delete": user.can_delete, "can_manage_catalog": user.can_manage_catalog,
            "can_see_reports": user.can_see_reports, "tools": [spec["name"] for spec in tools_for(user)]}


@tool("list_team", "Personas del equipo comercial, con su id, correo y rol. Sirve para asignar responsables.")
def list_team(user, args):
    return [{"id": person.pk, "name": person.name, "email": person.email, "role": person.role_name}
            for person in User.objects.filter(is_active=True).select_related("role")]


@tool("list_stages", "Etapas del pipeline en orden, con su probabilidad, y motivos de pérdida disponibles.")
def list_stages(user, args):
    return {"stages": [{"id": s.pk, "name": s.name, "probability": s.probability, "won": s.is_won, "lost": s.is_lost} for s in Stage.objects.all()],
            "loss_reasons": [{"id": r.pk, "name": r.name} for r in LossReason.objects.all()]}


@tool("list_events", "Ferias y eventos por fecha (los más recientes primero), con cuántas empresas de las que ves se conocieron en cada uno.")
def list_events(user, args):
    companies = scope(Company.objects.all(), user)
    return [{"id": e.pk, "name": e.name, "kind": e.get_kind_display(), "starts_on": e.starts_on.isoformat() if e.starts_on else None,
             "ends_on": e.ends_on.isoformat() if e.ends_on else None, "place": e.place, "companies": companies.filter(event=e).count()}
            for e in Event.objects.all()]


@tool("create_event", "Da de alta una feria o evento para poder asignarlo a las empresas conocidas allí.",
      {"name": S, "kind": desc(S, "Tipo.", enum=list(Event.Kind.values)), "starts_on": desc(S, "AAAA-MM-DD."), "ends_on": S, "place": S, "website": S, "notes": S},
      ["name"], needs="events", write=True)
def create_event(user, args):
    existing = Event.objects.filter(name__iexact=args["name"].strip()).first()
    if existing:
        return {"created": False, "id": existing.pk, "name": existing.name}
    event = Event(name=args["name"].strip(), created_by=user, kind=args.get("kind") or Event.Kind.FAIR)
    apply(event, args, ("place", "website", "notes"))
    for name in ("starts_on", "ends_on"):
        if args.get(name):
            setattr(event, name, to_date(args[name], name))
    event.save()
    return {"created": True, "id": event.pk, "name": event.name}


@tool("search_companies", "Busca empresas (clientes y posibles clientes) por nombre, CIF o dominio.",
      {"q": desc(S, "Texto a buscar. Vacío = las primeras por nombre."), "event": desc(S, "Solo las conocidas en esa feria o evento."), "limit": I})
def search_companies(user, args):
    q = (args.get("q") or "").strip()
    found = scope(Company.objects.select_related("owner", "event"), user)
    if args.get("event"):
        found = found.filter(event=find_event(args["event"]))
    if q:
        found = found.filter(Q(name__icontains=q) | Q(tax_id__icontains=q.replace(" ", "")) | Q(domain__icontains=q) | Q(name_key=normalize_name(q)))
    return [company_row(c) for c in found.order_by("name")[:limit_of(args)]]


@tool("get_company", "Ficha de una empresa: datos, contactos, oportunidades, tareas pendientes, suscripciones y última actividad.",
      {"company_id": I}, ["company_id"])
def get_company(user, args):
    company = fetch(Company, user, args["company_id"], "una empresa")
    opps = scope(Opportunity.objects.filter(company=company).select_related("company", "stage", "owner"), user)
    return {
        **company_row(company), "website": company.website, "email": company.email, "phone": company.phone, "sector": company.sector,
        "address": company.address, "province": company.province, "country": company.country, "notes": company.notes,
        "possible_duplicates": [company_row(c) for c in scope(company.duplicates(), user)[:5]],
        "contacts": [contact_row(c) for c in Contact.objects.filter(Q(company=company) | Q(other_companies=company)).distinct().select_related("company")[:30]],
        "opportunities": [opportunity_row(o) for o in opps[:30]],
        "pending_tasks": [task_row(t) for t in scope(Task.objects.filter(company=company, done_at__isnull=True).select_related("assignee"), user)[:20]],
        "subscriptions": [{"id": s.pk, "plan": s.plan_name, "mrr": money(s.mrr), "status": s.get_status_display(),
                           "renews_on": s.renews_on.isoformat() if s.renews_on else None} for s in company.subscriptions.select_related("product")],
        "hour_banks": [{"id": b.pk, "name": b.name, "hours_sold": money(b.hours_sold), "hours_left": money(b.hours_left)} for b in company.hour_banks.all()],
        "recent_activity": [activity_row(a) for a in company.activities.select_related("user")[:10]],
    }


@tool("search_contacts", "Busca personas por nombre, correo, cargo o empresa.",
      {"q": S, "company_id": desc(I, "Limita a una empresa."), "limit": I})
def search_contacts(user, args):
    found = scope(Contact.objects.select_related("company"), user)
    if args.get("company_id"):
        found = found.filter(Q(company_id=args["company_id"]) | Q(other_companies=args["company_id"]))
    for word in (args.get("q") or "").split():
        found = found.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(email__icontains=word)
                             | Q(position__icontains=word) | Q(company__name__icontains=word) | Q(other_companies__name__icontains=word)
                             | Q(channels__value__icontains=word))
    return [contact_row(c) for c in found.distinct().order_by("first_name", "last_name")[:limit_of(args)]]


@tool("list_opportunities", "Oportunidades (leads) con filtros. Por defecto, las abiertas.", {
    "q": desc(S, "Texto en el título o en la empresa."),
    "status": desc(S, "Estado. «hold» = paradas por el cliente.", enum=["open", "hold", "won", "lost", "all"]),
    "stage": desc(S, "Nombre o id de la etapa."),
    "owner": desc(S, "Responsable: id, correo o nombre. «me» = la persona conectada."),
    "next_action": desc(S, "Situación de la próxima acción.", enum=["none", "overdue", "today", "week"]),
    "product": desc(S, "Código de producto que lleva en sus líneas."),
    "priority": desc(I, "1 baja, 2 normal, 3 alta."),
    "company_id": I, "close_from": desc(S, "Cierre estimado desde (AAAA-MM-DD)."), "close_to": S, "limit": I,
})
def list_opportunities(user, args):
    query = {key: args[key] for key in ("q", "status", "priority", "close_from", "close_to") if args.get(key) not in (None, "")}
    query.setdefault("status", "open")
    if query["status"] == "hold":
        query.update(status="open", hold="only")
    if args.get("stage"):
        query["stage"] = find_stage(args["stage"]).pk
    if args.get("owner"):
        query["owner"] = user.pk if str(args["owner"]).lower() in ("me", "yo") else find_user(args["owner"], "persona responsable").pk
    if args.get("next_action"):
        query["action"] = args["next_action"]
    if args.get("company_id"):
        query["company"] = args["company_id"]
    if args.get("product"):
        product = Product.objects.filter(code__iexact=args["product"]).first()
        if product is None:
            raise ToolError(f"No existe el producto «{args['product']}». Usa list_products.")
        query["product"] = product.pk
    flt = OpportunityFilter(query, queryset=scope(Opportunity.objects.select_related("company", "stage", "owner"), user))
    if not flt.form.is_valid():
        raise ToolError("Filtros no válidos: " + "; ".join(f"{key}: {' '.join(errors)}" for key, errors in flt.form.errors.items()))
    rows = flt.qs.distinct().order_by("stage__order", "-priority", "expected_close")
    return {"count": rows.count(), "opportunities": [opportunity_row(o) for o in rows[:limit_of(args)]]}


@tool("get_opportunity", "Detalle de una oportunidad: líneas de producto, próxima acción, tareas, presupuestos e historial.",
      {"opportunity_id": I}, ["opportunity_id"])
def get_opportunity(user, args):
    opp = fetch(Opportunity, user, args["opportunity_id"], "una oportunidad")
    return {
        **opportunity_row(opp), "description": opp.description, "contact": contact_row(opp.contact) if opp.contact_id else None,
        "assignees": [str(person) for person in opp.assignees.all()], "can_edit": user.can_edit(opp),
        "loss_reason": str(opp.loss_reason) if opp.loss_reason_id else None, "loss_notes": opp.loss_notes,
        "lines": [line_row(line) for line in opp.lines.select_related("product")],
        "pending_tasks": [task_row(t) for t in opp.tasks.filter(done_at__isnull=True).select_related("assignee")],
        "quotes": [quote_row(q) for q in opp.quotes.select_related("opportunity__company")[:10]],
        "activity": [activity_row(a) for a in opp.activities.select_related("user")[:15]],
    }


@tool("pipeline_summary", "Resumen del pipeline visible para ti: por etapa, número de oportunidades y lo que hay en juego "
      "(MRR, horas y pago único por separado), más cuántas no tienen próxima acción o la tienen vencida.")
def pipeline_summary(user, args):
    opps = scope(Opportunity.objects.select_related("stage"), user).filter(stage__is_won=False, stage__is_lost=False)
    stages = {}
    for opp in opps:
        row = stages.setdefault(opp.stage_id, {"stage": opp.stage.name, "order": opp.stage.order, "count": 0, "mrr": Decimal(0),
                                               "hourly_services": Decimal(0), "one_off": Decimal(0)})
        row["count"] += 1
        row["mrr"] += opp.amount_mrr
        row["hourly_services"] += opp.amount_services
        row["one_off"] += opp.amount_one_off
    ordered = sorted(stages.values(), key=lambda row: row.pop("order"))
    for row in ordered:
        row.update(mrr=money(row["mrr"]), hourly_services=money(row["hourly_services"]), one_off=money(row["one_off"]))
    return {"scope": "todo el equipo" if user.sees_all else "tus oportunidades", "open": opps.count(), "by_stage": ordered,
            "without_next_action": opps.filter(next_action_at__isnull=True).count(),
            "overdue_next_action": opps.filter(next_action_at__lt=timezone.now()).count()}


@tool("list_tasks", "Tareas y recordatorios. Por defecto, las pendientes de la persona conectada.", {
    "when": desc(S, "Filtro de plazo.", enum=["overdue", "today", "week", "done"]),
    "assignee": desc(S, "Id, correo o nombre. «all» = de cualquiera que puedas ver."),
    "opportunity_id": I, "company_id": I, "limit": I,
})
def list_tasks(user, args):
    query = {key: args[key] for key in ("when",) if args.get(key)}
    assignee = str(args.get("assignee") or "me").lower()
    if assignee not in ("all", "todos"):
        query["assignee"] = user.pk if assignee in ("me", "yo") else find_user(args["assignee"]).pk
    for key, name in (("opportunity_id", "opportunity"), ("company_id", "company")):
        if args.get(key):
            query[name] = args[key]
    flt = TaskFilter(query, queryset=scope(Task.objects.select_related("assignee"), user))
    if not flt.form.is_valid():
        raise ToolError("Filtros no válidos: " + "; ".join(f"{key}: {' '.join(errors)}" for key, errors in flt.form.errors.items()))
    rows = flt.qs if args.get("when") == "done" else flt.qs.filter(done_at__isnull=True)
    return [task_row(t) for t in rows.order_by("due_at")[:limit_of(args)]]


@tool("list_activity", "Historial (notas, llamadas, correos, reuniones, cambios de etapa) de una empresa o de una oportunidad.",
      {"company_id": I, "opportunity_id": I, "limit": I})
def list_activity(user, args):
    found = scope(Activity.objects.select_related("user"), user)
    if args.get("opportunity_id"):
        found = found.filter(opportunity_id=args["opportunity_id"])
    elif args.get("company_id"):
        found = found.filter(company_id=args["company_id"])
    return [activity_row(a) for a in found.order_by("-occurred_at")[:limit_of(args)]]


@tool("list_products", "Catálogo de productos con su modelo de cobro (monthly, hourly, one_off) y precio. Precio nulo = a consultar.",
      {"q": S, "include_retired": B})
def list_products(user, args):
    found = Product.objects.select_related("family")
    if not args.get("include_retired"):
        found = found.exclude(status="retired")
    if args.get("q"):
        found = found.filter(Q(name__icontains=args["q"]) | Q(code__icontains=args["q"]) | Q(family__name__icontains=args["q"]))
    return [product_row(p) for p in found[:200]]


@tool("list_quotes", "Presupuestos (última versión de cada número) con su importe y estado.",
      {"q": desc(S, "Número, descripción o empresa."), "status": desc(S, "Estado.", enum=list(Quote.Status.values)),
       "opportunity_id": I, "limit": I})
def list_quotes(user, args):
    found = scope(Quote.objects.select_related("opportunity__company"), user)
    if args.get("opportunity_id"):
        found = found.filter(opportunity_id=args["opportunity_id"])
    if args.get("status"):
        found = found.filter(status=args["status"])
    if args.get("q"):
        q = args["q"]
        found = found.filter(Q(number__icontains=q) | Q(title__icontains=q) | Q(opportunity__company__name__icontains=q))
    return [quote_row(q) for q in found.order_by("-issued_on", "-version") if q.is_latest][:limit_of(args)]


@tool("get_quote", "Un presupuesto con sus líneas, notas y versiones.", {"quote_id": I}, ["quote_id"])
def get_quote(user, args):
    quote = fetch(Quote, user, args["quote_id"], "un presupuesto")
    return {**quote_row(quote), "bill_to": quote.bill_to, "intro": quote.intro, "notes": quote.terms, "tax_rate": money(quote.tax_rate),
            "lines": quote.lines, "sent_to": quote.sent_to,
            "versions": [{"id": v.pk, "version": v.version, "status": v.get_status_display(), "base": money(v.base)}
                         for v in Quote.objects.filter(number=quote.number).order_by("-version")]}


@tool("list_subscriptions", "Suscripciones (recurrente en cartera) y bolsas de horas de los clientes que ves.",
      {"company_id": I, "include_cancelled": B})
def list_subscriptions(user, args):
    subs = scope(Subscription.objects.select_related("company", "product"), user)
    banks = scope(HourBank.objects.select_related("company"), user)
    if args.get("company_id"):
        subs, banks = subs.filter(company_id=args["company_id"]), banks.filter(company_id=args["company_id"])
    if not args.get("include_cancelled"):
        subs, banks = subs.exclude(status=Subscription.Status.CANCELLED), banks.filter(closed=False)
    return {
        "subscriptions": [{"id": s.pk, "company": s.company.name, "plan": s.plan_name, "quantity": money(s.quantity), "mrr": money(s.mrr),
                           "period": s.period, "status": s.get_status_display(), "renews_on": s.renews_on.isoformat() if s.renews_on else None}
                          for s in subs[:100]],
        "hour_banks": [{"id": b.pk, "company": b.company.name, "name": b.name, "hours_sold": money(b.hours_sold),
                        "hours_used": money(b.hours_used), "hours_left": money(b.hours_left)} for b in banks[:100]],
    }


@tool("my_incentives", "Incentivos de la persona conectada (ventas, cuota recurrente y PM): previsto, devengado según lo cobrado, abonado y "
      "pendiente de validar. Quien gestiona incentivos puede pedir los de otra persona.",
      {"person": desc(S, "Solo para quien gestiona incentivos: id, correo o nombre.")})
def my_incentives(user, args):
    from apps.incentives.models import Incentive

    person = user
    if args.get("person"):
        if not user.can_manage_incentives:
            raise ToolError("Tu rol solo puede consultar sus propios incentivos.")
        person = find_user(args["person"])
    rows = list(Incentive.objects.filter(user=person))
    live = [i for i in rows if i.status == Incentive.Status.APPROVED]
    return {
        "person": str(person), "expected": money(sum((i.total for i in live), Decimal(0))), "earned": money(sum((i.earned for i in live), Decimal(0))),
        "paid": money(sum((i.paid for i in live), Decimal(0))), "due": money(sum((i.due for i in live), Decimal(0))),
        "incentives": [{"id": i.pk, "program": i.get_program_display(), "concept": i.concept, "status": i.get_status_display(), "total": money(i.total),
                        "earned": money(i.earned), "paid": money(i.paid), "collected_pct": money(i.collected_pct)} for i in rows],
    }


@tool("get_report", "Informe de dirección: valor del pipeline por etapa, conversión, motivos de pérdida, recurrente y previsión. "
      "Solo para el superusuario.", needs="reports")
def get_report(user, args):
    return json.loads(json.dumps(reports.summary(), default=str))


# ------------------------------------------------------------------ escritura
COMPANY_FIELDS = ("name", "tax_id", "website", "email", "phone", "sector", "address", "city", "province", "country", "notes")
COMPANY_PROPS = {"name": desc(S, "Razón social o nombre comercial."), "tax_id": desc(S, "CIF/NIF."), "website": S, "email": S, "phone": S,
                 "sector": S, "address": S, "city": S, "province": S, "country": S, "notes": S,
                 "kind": desc(S, "Tipo.", enum=list(Company.Kind.values)), "owner": desc(S, "Responsable: id, correo o nombre."),
                 "event": desc(S, "Feria o evento donde se la conoció: nombre o id (ver list_events).")}


def _save_company(user, company, args):
    apply(company, args, COMPANY_FIELDS)
    if args.get("kind"):
        company.kind = args["kind"]
    if args.get("event"):
        company.event = find_event(args["event"])
    if args.get("owner"):
        if not user.edits_all:
            raise ToolError("Tu rol no puede asignar empresas a otra persona.")
        company.owner = find_user(args["owner"], "persona responsable")
    company.save()
    return company


@tool("create_company", "Da de alta una empresa. Si ya existe una con el mismo nombre, CIF o dominio, no la duplica: devuelve la existente.",
      COMPANY_PROPS, ["name"], write=True)
def create_company(user, args):
    probe = Company(name=args["name"], tax_id=args.get("tax_id") or "", website=args.get("website") or "")
    probe.name_key = normalize_name(probe.name)
    existing = scope(Company.objects.all(), user).filter(name_key=probe.name_key).first() if probe.name_key else None
    if existing:
        return {"created": False, "note": "Ya existía una empresa con ese nombre; no se ha duplicado.", "company": company_row(existing)}
    company = _save_company(user, Company(owner=user), args)
    emit("company.created", {"id": company.pk, "name": company.name})
    return {"created": True, "company": company_row(company)}


@tool("update_company", "Cambia datos de una empresa. Solo se tocan los campos que envíes.",
      {"company_id": I, **COMPANY_PROPS}, ["company_id"], write=True)
def update_company(user, args):
    company = _save_company(user, editable(user, fetch(Company, user, args["company_id"], "una empresa")), args)
    emit("company.updated", {"id": company.pk, "name": company.name})
    return company_row(company)


CONTACT_FIELDS = ("first_name", "last_name", "position", "email", "phone", "mobile", "notes")
CONTACT_PROPS = {"first_name": S, "last_name": S, "position": desc(S, "Cargo."), "email": S, "phone": S, "mobile": S, "notes": S,
                 "legal_basis": desc(S, "Base legal RGPD.", enum=list(Contact.LegalBasis.values)),
                 "is_primary": desc(B, "Contacto principal de la empresa.")}


@tool("create_contact", "Da de alta una persona en una empresa. Si ya hay una con ese correo en la empresa, devuelve la existente.",
      {"company_id": I, **CONTACT_PROPS}, ["company_id", "first_name"], write=True)
def create_contact(user, args):
    company = fetch(Company, user, args["company_id"], "una empresa")
    email = (args.get("email") or "").strip().lower()
    existing = company.contacts.filter(email__iexact=email).first() if email else None
    if existing:
        return {"created": False, "note": "Ya había un contacto con ese correo en la empresa.", "contact": contact_row(existing)}
    contact = Contact(company=company, owner=user, is_primary=args.get("is_primary", not company.contacts.exists()))
    apply(contact, {**args, "email": email}, CONTACT_FIELDS)
    if args.get("legal_basis"):
        contact.legal_basis = args["legal_basis"]
    contact.save()
    emit("contact.created", {"id": contact.pk, "name": str(contact)})
    return {"created": True, "contact": contact_row(contact)}


@tool("update_contact", "Cambia datos de un contacto. Solo se tocan los campos que envíes.",
      {"contact_id": I, **CONTACT_PROPS}, ["contact_id"], write=True)
def update_contact(user, args):
    contact = editable(user, fetch(Contact, user, args["contact_id"], "un contacto"))
    if contact.erased_at:
        raise ToolError("Los datos de este contacto se suprimieron (RGPD) y no pueden volver a rellenarse.")
    apply(contact, args, CONTACT_FIELDS)
    for name in ("legal_basis", "is_primary"):
        if args.get(name) is not None:
            setattr(contact, name, args[name])
    contact.save()
    return contact_row(contact)


@tool("erase_contact", "RGPD, derecho de supresión: borra los datos personales de un contacto. No se puede deshacer y queda en el "
      "registro de accesos. Confirma siempre con la persona antes de usarla.", {"contact_id": I}, ["contact_id"],
      needs="delete", write=True, destructive=True)
def erase_contact(user, args):
    contact = editable(user, fetch(Contact, user, args["contact_id"], "un contacto"))
    AccessLog.objects.create(user=user, action=AccessLog.Action.ERASE, object_type="contact", object_id=str(contact.pk),
                             object_repr=str(contact)[:200], channel="mcp")
    contact.erase()
    return {"erased": True, "contact_id": contact.pk}


OPP_PROPS = {
    "title": S, "company_id": desc(I, "Empresa existente."), "company_name": desc(S, "Si no hay company_id: se busca por nombre y, si no existe, se crea."),
    "contact_id": I, "stage": desc(S, "Nombre o id de etapa. Por defecto, la primera."),
    "owner": desc(S, "Responsable: id, correo o nombre. Por defecto, la persona conectada."),
    "assignees": desc({"type": "array", "items": S}, "Otras personas asignadas (id, correo o nombre). Sustituye a las actuales."),
    "priority": desc(I, "1 baja, 2 normal, 3 alta."), "expected_close": desc(S, "AAAA-MM-DD."),
    "source": desc(S, "Origen: feria, web, referido…"), "description": S, "probability": desc(I, "0 a 100."),
}


def _save_opportunity(user, opp, args):
    apply(opp, args, ("title", "source", "description"))
    if args.get("contact_id"):
        contact = fetch(Contact, user, args["contact_id"], "un contacto")
        if contact.company_id and opp.company_id and not contact.works_at(opp.company):
            raise ToolError(f"{contact} es de otra empresa ({contact.company}).")
        opp.contact = contact
    if args.get("owner"):
        owner = find_user(args["owner"], "persona responsable")
        if owner != user and not user.edits_all:
            raise ToolError("Tu rol no puede poner a otra persona como responsable.")
        opp.owner = owner
    if args.get("priority") in (1, 2, 3):
        opp.priority = args["priority"]
    if args.get("expected_close"):
        opp.expected_close = to_date(args["expected_close"], "fecha de cierre")
    if args.get("probability") is not None and 0 <= int(args["probability"]) <= 100:
        opp.probability = int(args["probability"])
    opp.save()
    if args.get("assignees") is not None:
        opp.assignees.set([find_user(person) for person in args["assignees"]])
    return opp


@tool("create_opportunity", "Crea una oportunidad (lead). La empresa puede ser una existente o, con company_name, buscarse y crearse "
      "si no existe. Después conviene añadir líneas (add_line) y la próxima acción (create_task).", OPP_PROPS, ["title"], write=True)
@transaction.atomic
def create_opportunity(user, args):
    if args.get("company_id"):
        company = fetch(Company, user, args["company_id"], "una empresa")
    elif (args.get("company_name") or "").strip():
        key = normalize_name(args["company_name"])
        company = (scope(Company.objects.all(), user).filter(name_key=key).first() if key else None) or Company.objects.create(name=args["company_name"].strip(), owner=user)
    else:
        raise ToolError("Indica company_id o company_name.")
    stage = find_stage(args["stage"]) if args.get("stage") else Stage.objects.filter(is_won=False, is_lost=False).first()
    if not stage.is_open:
        raise ToolError("Una oportunidad nueva empieza en una etapa abierta. Para cerrarla usa move_opportunity.")
    opp = Opportunity(company=company, stage=stage, owner=user, probability=stage.probability)
    _save_opportunity(user, opp, args)
    history.record(user, opp)
    emit("opportunity.created", {"id": opp.pk, "name": str(opp)})
    return {**opportunity_row(opp), "note": "Sin próxima acción todavía: créala con create_task."}


@tool("update_opportunity", "Cambia datos de una oportunidad (no la etapa: para eso, move_opportunity). Solo los campos que envíes.",
      {"opportunity_id": I, **{k: v for k, v in OPP_PROPS.items() if k not in ("company_id", "company_name", "stage")}}, ["opportunity_id"], write=True)
def update_opportunity(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    before = history.snapshot(opp)
    _save_opportunity(user, opp, args)
    history.record(user, opp, before)
    emit("opportunity.updated", {"id": opp.pk, "name": str(opp)})
    return opportunity_row(opp)


@tool("move_opportunity", "Cambia una oportunidad de etapa. Ganarla crea sus suscripciones y bolsas de horas; perderla exige motivo.",
      {"opportunity_id": I, "stage": desc(S, "Nombre o id de la etapa de destino."),
       "loss_reason": desc(S, "Obligatorio al perder: nombre del motivo (ver list_stages)."), "loss_notes": S},
      ["opportunity_id", "stage"], write=True)
@transaction.atomic
def move_opportunity(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    stage, before, reason = find_stage(args["stage"]), opp.stage, None
    if stage.is_lost:
        reason = LossReason.objects.filter(name__iexact=str(args.get("loss_reason") or "").strip()).first()
        if reason is None:
            raise ToolError(f"Para perderla indica loss_reason. Motivos: {', '.join(LossReason.objects.values_list('name', flat=True))}.")
    won_now = opp.move_to(stage, user, reason, (args.get("loss_notes") or "")[:300])
    if won_now:
        services.fulfil_won(opp, user)
    if before != stage:
        emit("opportunity.stage_changed", {"id": opp.pk, "from": before.name, "to": stage.name})
        if won_now:
            emit("opportunity.won", {"id": opp.pk, "company": opp.company_id, "mrr": str(opp.amount_mrr),
                                     "services": str(opp.amount_services), "one_off": str(opp.amount_one_off)})
        if stage.is_lost:
            emit("opportunity.lost", {"id": opp.pk, "reason": str(reason)})
    return {**opportunity_row(opp), "moved": before != stage, "from": before.name}


@tool("hold_opportunity", "Para una oportunidad a la espera del cliente. No cambia de etapa. La fecha de revisión es obligatoria "
      "(máximo 90 días) y pasa a ser su próxima acción.",
      {"opportunity_id": I, "until": desc(S, "Fecha de revisión, AAAA-MM-DD."), "reason": desc(S, "Por qué está parada.")},
      ["opportunity_id", "until", "reason"], write=True)
def hold_opportunity(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    try:
        opp.hold(to_date(args["until"], "fecha de revisión"), args["reason"], user)
    except ValueError as exc:
        raise ToolError(str(exc))
    return opportunity_row(opp)


@tool("resume_opportunity", "Reanuda una oportunidad parada. Después conviene ponerle su próxima acción.",
      {"opportunity_id": I, "note": S}, ["opportunity_id"], write=True)
def resume_opportunity(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    opp.resume(user, args.get("note") or "")
    opp.refresh_from_db()
    return opportunity_row(opp)


@tool("add_line", "Añade una línea a una oportunidad: un producto del catálogo (product = su código) o una línea libre con su "
      "billing_model (monthly = recurrente, hourly = por horas, one_off = pago único), descripción y precio.", {
          "opportunity_id": I, "product": desc(S, "Código del producto del catálogo."), "description": S,
          "billing_model": desc(S, "Obligatorio en una línea libre.", enum=list(BillingModel.values)),
          "quantity": N, "unit_price": desc(N, "Vacío = precio del catálogo; sin precio = a consultar."),
          "discount_pct": N, "period": desc(S, "Solo recurrentes.", enum=list(OpportunityLine.Period.values)),
      }, ["opportunity_id"], write=True)
def add_line(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    product = None
    if args.get("product"):
        product = Product.objects.exclude(status="retired").filter(code__iexact=args["product"]).first()
        if product is None:
            raise ToolError(f"No existe el producto «{args['product']}» o está retirado. Usa list_products.")
    elif not (args.get("description") and args.get("billing_model")):
        raise ToolError("Una línea libre necesita description y billing_model.")
    period = args.get("period") or OpportunityLine.Period.MONTHLY
    price = args.get("unit_price")
    if price is None and product:
        price = product.price_annual if period == "annual" and product.price_annual is not None else product.price
    billing = args.get("billing_model") or product.billing_model
    line = OpportunityLine.objects.create(
        opportunity=opp, product=product, description=args.get("description") or "", billing_model=billing, period=period,
        quantity=to_decimal(args.get("quantity", 1), "quantity") or Decimal(1), unit_price=None if price is None else to_decimal(price, "unit_price"),
        discount=to_decimal(args.get("discount_pct", 0), "discount_pct") or Decimal(0),
        unit=product.unit if product else {"monthly": "mes", "hourly": "hora"}.get(billing, ""),
    )
    opp.refresh_from_db()
    return {"line": line_row(line), "opportunity": opportunity_row(opp)}


@tool("remove_line", "Quita una línea de una oportunidad.", {"line_id": I}, ["line_id"], write=True, destructive=True)
def remove_line(user, args):
    line = OpportunityLine.objects.select_related("opportunity").filter(pk=args["line_id"]).first()
    if line is None or not scope(Opportunity.objects.filter(pk=line.opportunity_id), user).exists():
        raise ToolError(f"No existe la línea {args['line_id']}, o no tienes acceso.")
    opp = editable(user, line.opportunity)
    line.delete()
    opp.refresh_from_db()
    return opportunity_row(opp)


@tool("log_activity", "Anota en el historial algo que ha pasado con un cliente: nota, llamada, correo o reunión. "
      "No envía nada a nadie.", {
          "kind": desc(S, "Tipo.", enum=["note", "call", "email", "meeting"]), "body": desc(S, "Qué ha pasado."), "subject": S,
          "opportunity_id": I, "company_id": desc(I, "Necesario si no hay opportunity_id."), "contact_id": I,
          "direction": desc(S, "Para llamadas y correos.", enum=["in", "out"]), "occurred_at": desc(S, "Cuándo fue. Por defecto, ahora."),
      }, ["kind", "body"], write=True)
def log_activity(user, args):
    opp = fetch(Opportunity, user, args["opportunity_id"], "una oportunidad") if args.get("opportunity_id") else None
    company = opp.company if opp else (fetch(Company, user, args["company_id"], "una empresa") if args.get("company_id") else None)
    if company is None:
        raise ToolError("Indica opportunity_id o company_id.")
    contact = fetch(Contact, user, args["contact_id"], "un contacto") if args.get("contact_id") else (opp.contact if opp else None)
    activity = Activity.objects.create(
        company=company, opportunity=opp, contact=contact, user=user, kind=args["kind"], body=args["body"], subject=(args.get("subject") or "")[:250],
        direction=args.get("direction") or "", occurred_at=to_datetime(args["occurred_at"]) if args.get("occurred_at") else timezone.now(), source="mcp",
    )
    mentions.notify(user, activity.body, activity=activity)
    return activity_row(activity)


@tool("create_task", "Crea una tarea o recordatorio. Con opportunity_id pasa a ser la próxima acción de esa oportunidad.", {
    "title": desc(S, "Qué hay que hacer."), "due_at": desc(S, "ISO 8601, fecha, o: hoy, mañana, +3d, +2w."),
    "kind": desc(S, "Tipo.", enum=list(Task.Kind.values)), "assignee": desc(S, "Id, correo o nombre. Por defecto, la persona conectada."),
    "also": desc({"type": "array", "items": S}, "Otras personas que comparten la acción (id, correo o nombre)."),
    "opportunity_id": I, "company_id": I, "notes": S,
}, ["title", "due_at"], write=True)
def create_task(user, args):
    opp = fetch(Opportunity, user, args["opportunity_id"], "una oportunidad") if args.get("opportunity_id") else None
    company = opp.company if opp else (fetch(Company, user, args["company_id"], "una empresa") if args.get("company_id") else None)
    assignee = find_user(args["assignee"], "persona asignada") if args.get("assignee") else user
    also = [find_user(person, "persona asignada") for person in args.get("also") or []]
    if any(u != user for u in [assignee, *also]) and not user.edits_all and not (opp and user.can_edit(opp)):
        raise ToolError("Tu rol solo puede crear tareas para otra persona en oportunidades tuyas.")
    task = Task.objects.create(title=args["title"][:200], due_at=to_datetime(args["due_at"], "fecha de la tarea"), kind=args.get("kind") or Task.Kind.TODO,
                               assignee=assignee, created_by=user, opportunity=opp, company=company, notes=args.get("notes") or "")
    task.assignees.set([u for u in also if u != assignee])
    mentions.notify(user, task.notes, task=task)
    return task_row(task)


@tool("complete_task", "Marca una tarea como hecha. Si era la próxima acción de una oportunidad, conviene programar la siguiente.",
      {"task_id": I, "reopen": desc(B, "true para volver a dejarla pendiente.")}, ["task_id"], write=True)
def complete_task(user, args):
    task = editable(user, fetch(Task, user, args["task_id"], "una tarea"))
    task.done_at = None if args.get("reopen") else timezone.now()
    task.save()
    row = task_row(task)
    if task.opportunity_id and task.done_at:
        opp = Opportunity.objects.get(pk=task.opportunity_id)
        if opp.is_open and not opp.next_action_at:
            row["note"] = "La oportunidad se ha quedado sin próxima acción: crea la siguiente con create_task."
    return row


@tool("reschedule_task", "Cambia la fecha, el texto o la persona de una tarea.",
      {"task_id": I, "due_at": S, "title": S, "assignee": S, "notes": S,
       "also": desc({"type": "array", "items": S}, "Otras personas que comparten la acción. Sustituye a las actuales.")}, ["task_id"], write=True)
def reschedule_task(user, args):
    task = editable(user, fetch(Task, user, args["task_id"], "una tarea"))
    apply(task, args, ("title", "notes"))
    if args.get("due_at"):
        task.due_at, task.reminded_at = to_datetime(args["due_at"], "fecha de la tarea"), None
    if args.get("assignee"):
        task.assignee = find_user(args["assignee"], "persona asignada")
    task.save()
    if args.get("also") is not None:
        task.assignees.set([u for u in (find_user(person, "persona asignada") for person in args["also"]) if u != task.assignee])
    return task_row(task)


QUOTE_LINE = {"type": "object", "additionalProperties": False, "required": ["description"], "properties": {
    "description": desc(S, "Las líneas que empiezan por «- » salen como lista de puntos."),
    "billing_model": desc(S, "Cobro.", enum=list(BillingModel.values)), "period": desc(S, "Solo recurrentes.", enum=["monthly", "annual"]),
    "quantity": N, "unit_price": desc(N, "Sin precio = a consultar."), "discount": desc(N, "Descuento en %.")}}


@tool("create_quote", "Genera un presupuesto en PDF para una oportunidad. Sin `lines`, usa las líneas de la oportunidad. Si la "
      "oportunidad ya tiene presupuesto, crea su versión siguiente: los presupuestos no se sobrescriben ni se borran.", {
          "opportunity_id": I, "title": desc(S, "Descripción breve que encabeza el documento."),
          "notes": desc(S, "Notas al pie. Por defecto, las de la plantilla."), "intro": S,
          "valid_until": desc(S, "AAAA-MM-DD."), "tax_rate": desc(N, "IVA en %. Por defecto, el de la plantilla."),
          "lines": desc({"type": "array", "items": QUOTE_LINE}, "Líneas propias del presupuesto."),
      }, ["opportunity_id"], write=True)
def create_quote(user, args):
    opp = editable(user, fetch(Opportunity, user, args["opportunity_id"], "una oportunidad"))
    previous = opp.quotes.order_by("-version").first()
    template = (previous.template if previous else None) or QuoteTemplate.objects.first()
    data = services.draft_from_opportunity(opp, template)
    if previous:
        data.update(title=previous.title or data["title"], bill_to=previous.bill_to or data["bill_to"], terms=previous.terms, intro=previous.intro)
    if args.get("title"):
        data["title"] = args["title"]
    if args.get("notes") is not None:
        data["terms"] = args["notes"]
    if args.get("intro") is not None:
        data["intro"] = args["intro"]
    if args.get("valid_until"):
        data["valid_until"] = to_date(args["valid_until"], "validez")
    if args.get("tax_rate") is not None:
        data["tax_rate"] = to_decimal(args["tax_rate"], "tax_rate")
    try:
        data["lines"] = services.clean_lines(args["lines"] if args.get("lines") else data["lines"])
        quote = services.save_quote(opp, user, template, data, previous)
    except ValueError as exc:
        raise ToolError(str(exc))
    return {**quote_row(quote), "url": f"/presupuestos/{quote.pk}/"}


@tool("send_quote", "ENVÍA un presupuesto por correo al cliente, con el PDF adjunto. Es lo único con lo que el CRM escribe a un "
      "cliente: confirma destinatario y mensaje con la persona antes de llamarla. El responsable y los asignados van en copia.", {
          "quote_id": I, "to": desc({"type": "array", "items": S}, "Destinatarios. Por defecto, el contacto de la oportunidad."),
          "subject": S, "message": desc(S, "Texto del correo."),
      }, ["quote_id", "message"], write=True, destructive=True)
def send_quote(user, args):
    from django.core.validators import validate_email

    quote = fetch(Quote, user, args["quote_id"], "un presupuesto")
    opp = editable(user, quote.opportunity)
    to = [address.strip().lower() for address in (args.get("to") or [])] or ([opp.contact.email] if opp.contact_id and opp.contact.email else [])
    if not to:
        raise ToolError("La oportunidad no tiene contacto con correo: indica `to`.")
    for address in to:
        try:
            validate_email(address)
        except Exception:
            raise ToolError(f"«{address}» no es un correo válido.")
    team = [opp.owner, *opp.assignees.all(), user]
    cc = [a for a in dict.fromkeys(p.email.lower() for p in team if p and p.is_active and p.email) if a not in to]
    subject = args.get("subject") or f"Presupuesto {quote.number}" + (f" · {quote.title}" if quote.title else "")
    try:
        services.send_quote(quote, user, to, cc, subject, args["message"])
    except Exception as exc:
        raise ToolError(f"No se ha podido enviar: {exc}")
    return {"sent": True, "to": to, "cc": cc, "quote": quote_row(quote)}


@tool("set_quote_status", "Marca un presupuesto como borrador, enviado, aceptado o rechazado.",
      {"quote_id": I, "status": desc(S, "Estado.", enum=list(Quote.Status.values))}, ["quote_id", "status"], write=True)
def set_quote_status(user, args):
    quote = fetch(Quote, user, args["quote_id"], "un presupuesto")
    editable(user, quote.opportunity)
    if args["status"] not in Quote.Status.values:
        raise ToolError(f"Estado no válido. Usa uno de: {', '.join(Quote.Status.values)}.")
    quote.status = args["status"]
    quote.save(update_fields=["status"])
    return quote_row(quote)


@tool("log_hours", "Apunta horas consumidas en una bolsa de horas de un cliente.",
      {"hour_bank_id": I, "hours": N, "description": desc(S, "Trabajo realizado."), "worked_on": desc(S, "AAAA-MM-DD. Por defecto, hoy.")},
      ["hour_bank_id", "hours", "description"], write=True)
def log_hours(user, args):
    bank = editable(user, fetch(HourBank, user, args["hour_bank_id"], "una bolsa de horas"))
    hours = to_decimal(args["hours"], "hours")
    if not hours or hours <= 0:
        raise ToolError("Las horas deben ser mayores que cero.")
    HourEntry.objects.create(bank=bank, hours=hours, description=args["description"][:300], user=user,
                             worked_on=to_date(args["worked_on"]) if args.get("worked_on") else timezone.localdate())
    return {"hour_bank_id": bank.pk, "name": bank.name, "hours_sold": money(bank.hours_sold), "hours_left": money(bank.hours_left)}


@tool("change_subscription", "Cambia cantidad, precio o plan de una suscripción. Queda como expansión o contracción.",
      {"subscription_id": I, "quantity": N, "unit_price": N, "product": desc(S, "Código del nuevo plan."), "note": S},
      ["subscription_id"], write=True)
def change_subscription(user, args):
    sub = editable(user, fetch(Subscription, user, args["subscription_id"], "una suscripción"))
    product = None
    if args.get("product"):
        product = Product.objects.filter(code__iexact=args["product"], billing_model=BillingModel.MONTHLY).first()
        if product is None:
            raise ToolError(f"«{args['product']}» no es un plan recurrente del catálogo.")
    event = sub.change(user, product, args.get("quantity"), args.get("unit_price"), (args.get("note") or "")[:300])
    emit("subscription.changed", {"id": sub.pk, "kind": event.kind, "company": sub.company_id, "mrr": str(sub.mrr)})
    return {"id": sub.pk, "plan": sub.plan_name, "mrr": money(sub.mrr), "change": event.get_kind_display()}


@tool("cancel_subscription", "Da de baja una suscripción: deja de contar en el recurrente. Confirma antes con la persona.",
      {"subscription_id": I, "note": S}, ["subscription_id"], write=True, destructive=True)
def cancel_subscription(user, args):
    sub = editable(user, fetch(Subscription, user, args["subscription_id"], "una suscripción"))
    sub.cancel(user, (args.get("note") or "")[:300])
    emit("subscription.changed", {"id": sub.pk, "kind": "cancelled", "company": sub.company_id, "mrr": "0"})
    return {"id": sub.pk, "plan": sub.plan_name, "status": sub.get_status_display()}


PRODUCT_PROPS = {"code": S, "name": S, "description": S, "family": desc(S, "Nombre de la familia; se crea si no existe."),
                 "billing_model": desc(S, "Cobro.", enum=list(BillingModel.values)), "unit": desc(S, "mes, hora, proyecto…"),
                 "price": desc(N, "Sin precio = a consultar."), "price_annual": N, "status": desc(S, "Estado.", enum=["active", "draft", "retired"])}


def _save_product(product, args):
    apply(product, args, ("code", "name", "description", "billing_model", "unit", "status"))
    for name in ("price", "price_annual"):
        if name in args:
            setattr(product, name, None if args[name] is None else to_decimal(args[name], name))
    if args.get("family"):
        product.family = ProductFamily.objects.filter(name__iexact=args["family"]).first() or ProductFamily.objects.create(name=args["family"])
    product.save()
    return product_row(product)


@tool("create_product", "Añade un producto al catálogo. Solo para roles que gestionan el catálogo.", PRODUCT_PROPS,
      ["code", "name", "billing_model"], needs="catalog", write=True)
def create_product(user, args):
    if Product.objects.filter(code__iexact=args["code"]).exists():
        raise ToolError(f"Ya existe un producto con el código {args['code']}. Usa update_product.")
    return _save_product(Product(), args)


@tool("update_product", "Cambia un producto del catálogo (precio, estado…). No altera oportunidades ni presupuestos ya hechos.",
      {"product": desc(S, "Código actual del producto."), **{k: v for k, v in PRODUCT_PROPS.items() if k != "code"}},
      ["product"], needs="catalog", write=True)
def update_product(user, args):
    product = Product.objects.filter(code__iexact=args["product"]).first()
    if product is None:
        raise ToolError(f"No existe el producto «{args['product']}».")
    return _save_product(product, {k: v for k, v in args.items() if k != "product"})


# ------------------------------------------------------------------ protocolo
def _authenticate(request):
    """Clave de API personal (qcrm_…) o access token OAuth emitido por este mismo servidor."""
    from .oauth import user_from_access_token

    header = request.META.get("HTTP_AUTHORIZATION", "").split()
    if len(header) != 2 or header[0].lower() != "bearer":
        return None
    if not header[1].startswith("qcrm_"):
        return user_from_access_token(header[1])
    key = ApiKey.objects.select_related("user", "user__role").filter(key_hash=hash_key(header[1]), revoked_at__isnull=True).first()
    if not key or not key.user.is_active:
        return None
    ApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
    return key.user


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _call(request, user, params):
    spec = {s["name"]: s for s in tools_for(user)}.get(params.get("name"))
    if spec is None:
        return {"content": [{"type": "text", "text": f"La herramienta «{params.get('name')}» no existe o tu rol no la tiene permitida."}], "isError": True}
    args = params.get("arguments") or {}
    unknown = set(args) - set(spec["schema"]["properties"])
    missing = [name for name in spec["schema"]["required"] if args.get(name) in (None, "")]
    try:
        if unknown:
            raise ToolError(f"Parámetros desconocidos: {', '.join(sorted(unknown))}.")
        if missing:
            raise ToolError(f"Faltan parámetros obligatorios: {', '.join(missing)}.")
        with transaction.atomic():
            data = spec["handler"](user, args)
        if spec["write"]:
            request.user = user
            log_access(request, AccessLog.Action.UPDATE, channel="mcp", label=spec["name"])
    except ToolError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except (TypeError, ValueError, KeyError) as exc:
        return {"content": [{"type": "text", "text": f"Parámetros no válidos: {exc}"}], "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}], "isError": False}


def _handle(request, user, message):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Petición JSON-RPC no válida.")
    method, request_id, params = message["method"], message.get("id"), message.get("params") or {}
    if "id" not in message:  # notificación: no lleva respuesta
        return None
    if method == "initialize":
        wanted = params.get("protocolVersion")
        return _result(request_id, {
            "protocolVersion": wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO, "instructions": INSTRUCTIONS})
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [
            {"name": spec["name"], "description": spec["description"], "inputSchema": spec["schema"],
             "annotations": {"readOnlyHint": not spec["write"], "destructiveHint": spec["destructive"], "openWorldHint": spec["name"] == "send_quote"}}
            for spec in tools_for(user)]})
    if method == "tools/call":
        return _result(request_id, _call(request, user, params))
    return _error(request_id, -32601, f"Método no soportado: {method}")


@csrf_exempt
@login_not_required
def endpoint(request):
    """POST /mcp/ — un mensaje JSON-RPC (o un lote) por petición. Autenticación: Authorization: Bearer qcrm_…"""
    if request.method != "POST":
        return HttpResponse(status=405, headers={"Allow": "POST"})
    user = _authenticate(request)
    if user is None:
        from .oauth import metadata_url

        # el 401 anuncia dónde está la metadata OAuth: así un conector arranca solo el flujo de autorización
        return JsonResponse(_error(None, -32001, "Hace falta autorización: OAuth o una clave de API (Authorization: Bearer qcrm_…)."), status=401,
                            headers={"WWW-Authenticate": f'Bearer realm="QopenCRM", resource_metadata="{metadata_url()}"'})
    if not user.can_use_api:
        return JsonResponse(_error(None, -32002, "Tu rol no tiene permitido el acceso por API ni MCP."), status=403)
    try:
        payload = json.loads(request.body or b"")
    except ValueError:
        return JsonResponse(_error(None, -32700, "JSON no válido."), status=400)
    if isinstance(payload, list):
        answers = [answer for answer in (_handle(request, user, message) for message in payload) if answer is not None]
        return JsonResponse(answers, safe=False) if answers else HttpResponse(status=202)
    answer = _handle(request, user, payload)
    return HttpResponse(status=202) if answer is None else JsonResponse(answer)
