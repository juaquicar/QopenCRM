import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from apps.accounts.models import AccessLog, Role, User, log_access
from apps.accounts.permissions import scope
from apps.catalog.models import BillingModel, Product, ProductFamily
from apps.incentives import forms as incentive_forms
from apps.integrations import ai, mcp, microsoft, oauth
from apps.integrations.models import ApiKey, MicrosoftAccount, MicrosoftConfig, Webhook, WebhookDelivery
from apps.integrations.webhooks import emit
from apps.sales import services
from apps.sales.models import HourBank, HourEntry, Quote, QuoteTemplate, Subscription

from . import forms, manual, reports
from .exporters import export_zip
from .filters import CompanyFilter, ContactFilter, OpportunityFilter, TaskFilter
from .importers import import_csv
from .models import Activity, Company, Contact, Event, LossReason, Opportunity, OpportunityLine, Stage, Task


def superuser_required(view):
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return inner


def refresh():
    """Respuesta htmx: cierra el diálogo y recarga la página."""
    return HttpResponse(status=204, headers={"HX-Refresh": "true"})


def go(url):
    return HttpResponse(status=204, headers={"HX-Redirect": url})


def visible(model, user):
    return scope(model.objects.all(), user)


def editable_or_403(user, obj):
    if not user.can_edit(obj):
        raise PermissionDenied


# --------------------------------------------------------------------- Hoy
def home(request):
    user = request.user
    now = timezone.now()
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    mine = Task.objects.filter(assignee=user, done_at__isnull=True).select_related("opportunity", "company")
    open_opps = visible(Opportunity, user).filter(stage__is_won=False, stage__is_lost=False).select_related("company", "stage", "owner")
    my_opps = open_opps.filter(Q(owner=user) | Q(assignees=user)).distinct()
    return render(request, "crm/home.html", {
        "overdue": mine.filter(due_at__lt=start),
        "today": mine.filter(due_at__gte=start, due_at__lt=start + timedelta(days=1)),
        "upcoming": mine.filter(due_at__gte=start + timedelta(days=1), due_at__lt=start + timedelta(days=8)),
        "no_action": (open_opps if user.sees_all else my_opps).filter(next_action_at__isnull=True)[:30],
        "my_opps_count": my_opps.count(),
        "recent": scope(Activity.objects.all(), user).select_related("company", "user", "opportunity")[:12],
        "now": now,
    })


# ---------------------------------------------------------------- Pipeline
def pipeline(request):
    user = request.user
    params = request.GET.copy()
    params.setdefault("status", "open")
    # Por defecto cada uno ve lo suyo, sea cual sea su rol; «Todo el equipo» envía assignee vacío.
    if "assignee" not in params:
        params["assignee"] = str(user.pk)
    base = visible(Opportunity, user).select_related("company", "stage", "owner").prefetch_related(
        Prefetch("tasks", queryset=Task.objects.filter(done_at__isnull=True).order_by("due_at"), to_attr="open_tasks")
    )
    flt = OpportunityFilter(params, queryset=base)
    opps = list(flt.qs.order_by("position", "-updated_at"))
    view = request.GET.get("view", "board")
    stages = list(Stage.objects.all())
    if params["status"] == "open":
        stages = [s for s in stages if s.is_open]
    columns = []
    for stage in stages:
        items = [o for o in opps if o.stage_id == stage.pk]
        columns.append({
            "stage": stage, "items": items,
            "mrr": sum(o.amount_mrr for o in items),
            "other": sum(o.amount_services + o.amount_one_off for o in items),
        })
    active = [k for k, v in request.GET.items() if v and k not in ("view", "status", "hold") and not (k == "assignee" and v == str(user.pk))]
    held = visible(Opportunity, user).filter(stage__is_won=False, stage__is_lost=False, on_hold_until__isnull=False)
    if params["assignee"].isdigit():
        held = held.filter(Q(owner_id=params["assignee"]) | Q(assignees=params["assignee"])).distinct()
    context = {"filter": flt, "columns": columns, "opps": opps, "view": view, "active_filters": active, "status": params["status"], "hold": params.get("hold", ""), "ai_enabled": ai.enabled(),
               "terminal_stages": Stage.objects.filter(Q(is_won=True) | Q(is_lost=True)), "now": timezone.now(),
               "n_hold": held.count()}
    template = "crm/_board.html" if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted") else "crm/pipeline.html"
    return render(request, template, context)


def opportunity_detail(request, pk):
    opp = get_object_or_404(visible(Opportunity, request.user).select_related("company", "stage", "owner", "contact"), pk=pk)
    log_access(request, AccessLog.Action.VIEW, opp)
    return render(request, "crm/opportunity.html", {
        "opp": opp, "company": opp.company,
        "stages": Stage.objects.all(),
        "lines": opp.lines.select_related("product"),
        "tasks": opp.tasks.select_related("assignee").order_by("done_at", "due_at"),
        "timeline": opp.activities.select_related("user", "contact"),
        "quotes": opp.quotes.all(),
        "templates": QuoteTemplate.objects.all(),
        "can_edit": request.user.can_edit(opp),
        "users": User.objects.filter(is_active=True),
        "now": timezone.now(),
    })


@require_POST
def opportunity_move(request, pk):
    """Arrastrar una tarjeta o pulsar una etapa. Perder exige motivo: se abre el diálogo."""
    opp = get_object_or_404(visible(Opportunity, request.user), pk=pk)
    editable_or_403(request.user, opp)
    stage = get_object_or_404(Stage, pk=request.POST.get("stage"))
    if stage.is_lost and stage != opp.stage:
        return HttpResponse(status=204, headers={"HX-Trigger": '{"openModal": "%s"}' % reverse("opportunity_lose", args=[opp.pk, stage.pk])})
    before = opp.stage
    with transaction.atomic():
        won_now = opp.move_to(stage, request.user)
        if request.POST.get("position", "").isdigit():
            Opportunity.objects.filter(pk=opp.pk).update(position=int(request.POST["position"]))
        if won_now:
            services.fulfil_won(opp, request.user)
        if before != stage:
            emit("opportunity.stage_changed", {"id": opp.pk, "from": before.name, "to": stage.name})
            if won_now:
                emit("opportunity.won", {"id": opp.pk, "company": opp.company_id, "mrr": str(opp.amount_mrr),
                                         "services": str(opp.amount_services), "one_off": str(opp.amount_one_off)})
    if request.POST.get("from") == "board":
        return HttpResponse(status=204, headers={"HX-Trigger": "boardChanged"})
    return refresh()


def opportunity_lose(request, pk, stage_pk):
    opp = get_object_or_404(visible(Opportunity, request.user), pk=pk)
    editable_or_403(request.user, opp)
    stage = get_object_or_404(Stage, pk=stage_pk, is_lost=True)
    form = forms.LoseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        opp.move_to(stage, request.user, form.cleaned_data["loss_reason"], form.cleaned_data["loss_notes"])
        emit("opportunity.lost", {"id": opp.pk, "reason": str(form.cleaned_data["loss_reason"])})
        return refresh()
    return render(request, "crm/_modal_form.html", {
        "form": form, "title": f"Perder «{opp.title}»", "action": request.path,
        "hint": "Indica el motivo: es lo que alimenta el informe de pérdidas.", "submit": "Marcar como perdida",
    })


def opportunity_hold(request, pk):
    """Parada por el cliente: no cambia de etapa, pero exige una fecha de revisión (máximo 90 días)."""
    opp = get_object_or_404(visible(Opportunity, request.user), pk=pk)
    editable_or_403(request.user, opp)
    if not opp.is_open:
        raise Http404
    default = timezone.localdate() + timedelta(days=30)
    form = forms.HoldForm(request.POST or None, initial={"until": opp.on_hold_until or default, "reason": opp.on_hold_reason})
    if request.method == "POST" and form.is_valid():
        opp.hold(form.cleaned_data["until"], form.cleaned_data["reason"], request.user)
        log_access(request, AccessLog.Action.UPDATE, opp)
        return refresh()
    limit = timezone.localdate() + timedelta(days=Opportunity.MAX_HOLD_DAYS)
    return render(request, "crm/_modal_form.html", {
        "form": form, "action": request.path, "submit": "Cambiar la revisión" if opp.on_hold else "Parar",
        "title": f"{'Parada' if opp.on_hold else 'Parar'} «{opp.title}»",
        "hint": f"Sigue en su etapa, pero deja de contar como oportunidad sin atender. El día de la revisión te saldrá en Hoy. "
                f"Fecha límite: {limit:%d/%m/%Y}." + (f" Ya se ha parado {opp.hold_count} veces: valora si toca darla por perdida." if opp.hold_count >= 2 else ""),
    })


@require_POST
def opportunity_resume(request, pk):
    opp = get_object_or_404(visible(Opportunity, request.user), pk=pk)
    editable_or_403(request.user, opp)
    opp.resume(request.user)
    messages.success(request, "Oportunidad reanudada. Ponle su próxima acción.")
    return redirect("opportunity", pk=pk)


@require_POST
def quote_create(request, pk):
    """Presupuesto directo desde la oportunidad, sin pasar por el editor."""
    opp = get_object_or_404(visible(Opportunity, request.user), pk=pk)
    editable_or_403(request.user, opp)
    if not opp.lines.exists():
        messages.error(request, "Añade al menos una línea de producto antes de generar el presupuesto.")
        return redirect("opportunity", pk=pk)
    template = QuoteTemplate.objects.filter(pk=request.POST.get("template") or 0).first()
    quote = services.create_quote(opp, request.user, template)
    messages.success(request, f"Presupuesto {quote} generado.")
    return redirect("opportunity", pk=pk)


def quote_list(request):
    """Presupuestos: la última versión de cada número. No hay borrado; las versiones anteriores se conservan."""
    newer = Quote.objects.filter(number=OuterRef("number"), version__gt=OuterRef("version"))
    quotes = (scope(Quote.objects.all(), request.user).annotate(superseded=Exists(newer)).filter(superseded=False)
              .select_related("opportunity__company", "created_by").order_by("-issued_on", "-id"))
    q, status = request.GET.get("q", "").strip(), request.GET.get("status", "")
    if q:
        quotes = quotes.filter(Q(number__icontains=q) | Q(title__icontains=q) | Q(opportunity__company__name__icontains=q) | Q(opportunity__title__icontains=q))
    if status in Quote.Status.values:
        quotes = quotes.filter(status=status)
    page = Paginator(quotes, 50).get_page(request.GET.get("page"))
    return render(request, "crm/quotes.html", {"page": page, "q": q, "status": status, "statuses": Quote.Status.choices})


def quote_detail(request, pk):
    quote = get_object_or_404(scope(Quote.objects.select_related("opportunity__company", "opportunity__contact", "created_by", "template"), request.user), pk=pk)
    log_access(request, AccessLog.Action.VIEW, quote)
    return render(request, "crm/quote.html", {
        "quote": quote, "opp": quote.opportunity, "can_edit": request.user.can_edit(quote.opportunity),
        "versions": Quote.objects.filter(number=quote.number).select_related("created_by").order_by("-version"),
    })


def _quote_form_data(post):
    """Lo que viene del editor, ya validado. Lanza ValueError con un mensaje para la persona."""
    keys = ("code", "description", "billing_model", "period", "quantity", "unit", "unit_price", "discount")
    columns = [post.getlist(f"line_{key}") for key in keys]
    lines = services.clean_lines([dict(zip(keys, row)) for row in zip(*columns)])
    issued = parse_date(post.get("issued_on", "")) or timezone.localdate()
    valid = parse_date(post.get("valid_until", "")) if post.get("valid_until") else None
    if valid and valid < issued:
        raise ValueError("La validez no puede terminar antes de la fecha del presupuesto.")
    tax = services.to_decimal(post.get("tax_rate"), Decimal(21))
    if not 0 <= tax <= 100:
        raise ValueError("El IVA debe estar entre 0 y 100.")
    return {
        "number": post.get("number", "").strip()[:20], "title": post.get("title", "").strip(), "bill_to": post.get("bill_to", "").strip(),
        "issued_on": issued, "valid_until": valid, "intro": post.get("intro", "").strip(), "terms": post.get("terms", "").rstrip(),
        "tax_rate": tax, "lines": lines,
    }


def _quote_editor(request, opportunity, template, initial, previous=None):
    """Editor común a «nuevo» y «editar». Guardar siempre crea una versión: nada se sobrescribe."""
    editable_or_403(request.user, opportunity)
    error, data = "", initial
    if request.method == "POST":
        template = QuoteTemplate.objects.filter(pk=request.POST.get("template") or 0).first() or template
        try:
            data = _quote_form_data(request.POST)
            quote = services.save_quote(opportunity, request.user, template, data, previous)
        except ValueError as exc:
            error = str(exc)
            data = {**initial, **{key: request.POST.get(key, "") for key in ("number", "title", "bill_to", "issued_on", "valid_until", "intro", "terms", "tax_rate")},
                    "lines": [dict(zip(("code", "description", "billing_model", "period", "quantity", "unit", "unit_price", "discount"), row))
                              for row in zip(*[request.POST.getlist(f"line_{key}") for key in ("code", "description", "billing_model", "period", "quantity", "unit", "unit_price", "discount")])]}
        else:
            messages.success(request, f"Presupuesto {quote} guardado." if quote.version == 1 else f"Versión {quote.version} de {quote.number} creada. La anterior se conserva.")
            return redirect("quote", pk=quote.pk)
    catalog = [{"id": p.pk, "code": p.code, "name": p.name, "billing_model": p.billing_model, "unit": p.unit,
                "price": None if p.price is None else str(p.price), "annual": None if p.price_annual is None else str(p.price_annual)}
               for p in Product.objects.exclude(status="retired")]
    return render(request, "crm/quote_edit.html", {
        "opp": opportunity, "previous": previous, "data": data, "error": error, "template": template,
        "templates": QuoteTemplate.objects.all(), "catalog": catalog, "billing_models": BillingModel.choices,
    })


def quote_new(request):
    """Presupuesto nuevo: se elige la oportunidad y se parte de sus líneas y de la plantilla."""
    opps = visible(Opportunity, request.user).filter(stage__is_lost=False).select_related("company").order_by("company__name", "title")
    opp_pk = request.GET.get("opportunity") or request.POST.get("opportunity")
    templates = QuoteTemplate.objects.all()
    if not opp_pk:
        opps = [o for o in opps if request.user.can_edit(o)]
        return render(request, "crm/quote_new.html", {"opps": opps, "templates": templates})
    opportunity = get_object_or_404(visible(Opportunity, request.user), pk=opp_pk)
    template = templates.filter(pk=request.GET.get("template") or 0).first() or templates.first()
    previous = opportunity.quotes.order_by("-version").first()
    if previous:  # ya tiene presupuesto: lo que toca es una versión nueva del suyo
        return redirect("quote_edit", pk=previous.pk)
    return _quote_editor(request, opportunity, template, services.draft_from_opportunity(opportunity, template))


def quote_edit(request, pk):
    """Editar un presupuesto crea su siguiente versión a partir de la que se está viendo."""
    quote = get_object_or_404(scope(Quote.objects.select_related("opportunity__company", "template"), request.user), pk=pk)
    initial = {"number": quote.number, "title": quote.title, "bill_to": quote.bill_to or services.default_bill_to(quote.opportunity.company),
               "issued_on": timezone.localdate(), "valid_until": quote.valid_until, "intro": quote.intro, "terms": quote.terms,
               "tax_rate": quote.tax_rate, "lines": quote.lines}
    if quote.valid_until and quote.valid_until < timezone.localdate():
        initial["valid_until"] = timezone.localdate() + timedelta(days=quote.template.validity_days if quote.template else 30)
    return _quote_editor(request, quote.opportunity, quote.template, initial, previous=quote)


def quote_send(request, pk):
    """Envía el PDF por correo al cliente desde el buzón del CRM, con respuesta a quien lo envía."""
    quote = get_object_or_404(scope(Quote.objects.select_related("opportunity__company", "opportunity__contact"), request.user), pk=pk)
    editable_or_403(request.user, quote.opportunity)
    contact = quote.opportunity.contact
    first_name = contact.first_name if contact else ""
    # el responsable y los asignados van siempre en copia, aunque se borren del campo
    team = [quote.opportunity.owner, *quote.opportunity.assignees.all(), request.user]
    always_cc = list(dict.fromkeys(person.email.lower() for person in team if person and person.is_active and person.email))
    form = forms.QuoteSendForm(request.POST or None, initial={
        "to": contact.email if contact and contact.email else quote.opportunity.company.email, "cc": ", ".join(always_cc),
        "subject": f"Presupuesto {quote.number}" + (f" · {quote.title}" if quote.title else ""),
        "body": f"Hola{' ' + first_name if first_name else ''}:\n\nTe adjunto el presupuesto {quote.number}"
                + (f" ({quote.title})" if quote.title else "") + ". Quedo a tu disposición para cualquier duda.\n\nUn saludo,\n"
                + (request.user.name or "QopenCRM"),
    })
    if request.method == "POST" and form.is_valid():
        try:
            to = list(dict.fromkeys(address.lower() for address in form.cleaned_data["to"]))
            cc = [address for address in dict.fromkeys([*always_cc, *(a.lower() for a in form.cleaned_data["cc"])]) if address not in to]
            services.send_quote(quote, request.user, to, cc, form.cleaned_data["subject"], form.cleaned_data["body"])
        except Exception as exc:  # el servidor de correo puede fallar: se dice, no se da por enviado
            form.add_error(None, f"No se ha podido enviar: {exc}")
        else:
            log_access(request, AccessLog.Action.EXPORT, quote, label="quote_sent")
            messages.success(request, f"Presupuesto {quote} enviado a {', '.join(form.cleaned_data['to'])}.")
            return refresh()
    return render(request, "crm/_modal_form.html", {
        "form": form, "title": f"Enviar {quote}", "action": request.path, "submit": "Enviar", "wide": True,
        "hint": "Se envía con el PDF adjunto. El responsable y los asignados de la oportunidad van siempre en copia. Las respuestas del cliente te llegan a ti.",
    })


@xframe_options_sameorigin
def quote_pdf(request, pk):
    quote = get_object_or_404(scope(Quote.objects.all(), request.user), pk=pk)
    if not quote.pdf:
        raise Http404
    log_access(request, AccessLog.Action.VIEW, quote)
    return FileResponse(quote.pdf.open("rb"), content_type="application/pdf", filename=f"Presupuesto {quote.number} v{quote.version}.pdf")


@require_POST
def quote_status(request, pk):
    quote = get_object_or_404(scope(Quote.objects.all(), request.user), pk=pk)
    editable_or_403(request.user, quote.opportunity)
    if request.POST.get("status") in Quote.Status.values:
        quote.status = request.POST["status"]
        quote.save(update_fields=["status"])
    if request.POST.get("back") == "quote":
        return redirect("quote", pk=quote.pk)
    return redirect("opportunity", pk=quote.opportunity_id)


# -------------------------------------------------------- Ferias y eventos
def event_list(request):
    """Ferias y eventos por fecha: primero los próximos (el más cercano arriba), después los pasados (el más reciente arriba)."""
    companies = visible(Company, request.user)
    opps = visible(Opportunity, request.user)
    today = timezone.localdate()
    events = list(Event.objects.all())
    for event in events:
        event.n_companies = companies.filter(event=event).count()
        related = opps.filter(company__event=event)
        event.n_open = related.filter(stage__is_won=False, stage__is_lost=False).count()
        event.n_won = related.filter(stage__is_won=True).count()
        event.last_day = event.ends_on or event.starts_on
    upcoming = sorted((e for e in events if e.last_day and e.last_day >= today), key=lambda e: (e.starts_on or e.last_day, e.name))
    past = sorted((e for e in events if e.last_day and e.last_day < today), key=lambda e: (e.starts_on or e.last_day, e.name), reverse=True)
    undated = sorted((e for e in events if not e.last_day), key=lambda e: e.name)
    return render(request, "crm/events.html", {"groups": [("Próximos", upcoming), ("Pasados", past), ("Sin fecha", undated)], "any": bool(events)})


def event_detail(request, pk):
    event = get_object_or_404(Event, pk=pk)
    companies = visible(Company, request.user).filter(event=event).select_related("owner").annotate(
        n_open=Count("opportunities", filter=Q(opportunities__stage__is_won=False, opportunities__stage__is_lost=False), distinct=True),
        n_contacts=Count("contacts", distinct=True))
    opps = visible(Opportunity, request.user).filter(company__event=event).select_related("company", "stage", "owner")
    return render(request, "crm/event.html", {
        "event": event, "companies": companies, "opps": opps,
        "can_edit": request.user.can_manage_events,
        "totals": {"open": sum(1 for o in opps if o.is_open), "won": sum(1 for o in opps if o.stage.is_won),
                   "mrr": sum((o.amount_mrr for o in opps if o.is_open), Decimal(0)),
                   "one_off": sum((o.amount_one_off + o.amount_services for o in opps if o.is_open), Decimal(0))},
    })


# ----------------------------------------------------------------- Cuentas
def company_list(request):
    base = visible(Company, request.user).select_related("owner").annotate(
        n_open=Count("opportunities", filter=Q(opportunities__stage__is_won=False, opportunities__stage__is_lost=False), distinct=True),
        n_contacts=Count("contacts", distinct=True),
    )
    flt = CompanyFilter(request.GET, queryset=base)
    page = Paginator(flt.qs.order_by("name"), 50).get_page(request.GET.get("page"))
    template = "crm/_company_rows.html" if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted") else "crm/companies.html"
    return render(request, template, {"filter": flt, "page": page})


def company_detail(request, pk):
    company = get_object_or_404(visible(Company, request.user).select_related("owner"), pk=pk)
    log_access(request, AccessLog.Action.VIEW, company)
    return render(request, "crm/company.html", {
        "company": company,
        "contacts": company.contacts.all(),
        "opps": visible(Opportunity, request.user).filter(company=company).select_related("stage", "owner"),
        "tasks": company.tasks.filter(done_at__isnull=True).select_related("assignee", "opportunity"),
        "timeline": company.activities.select_related("user", "contact", "opportunity")[:200],
        "subscriptions": company.subscriptions.select_related("product"),
        "hour_banks": company.hour_banks.select_related("product"),
        "duplicates": company.duplicates()[:5],
        "can_edit": request.user.can_edit(company),
        "users": User.objects.filter(is_active=True),
        "now": timezone.now(),
    })


@require_POST
@transaction.atomic
def company_merge(request, pk, other_pk):
    """Deduplicación: lo de la otra cuenta pasa a esta y la otra desaparece."""
    if not (request.user.edits_all and request.user.can_delete):
        raise PermissionDenied
    company = get_object_or_404(Company, pk=pk)
    other = get_object_or_404(Company, pk=other_pk)
    if other == company:
        raise Http404
    for rel in ("contacts", "opportunities", "activities", "tasks", "subscriptions", "hour_banks"):
        getattr(other, rel).update(company=company)
    for field in ("tax_id", "website", "email", "phone", "address", "city", "province", "sector", "owner"):
        if not getattr(company, field) and getattr(other, field):
            setattr(company, field, getattr(other, field))
    if other.notes:
        company.notes = f"{company.notes}\n{other.notes}".strip()
    company.save()
    Activity.objects.create(company=company, user=request.user, kind=Activity.Kind.SYSTEM, subject=f"Fusionada con «{other.name}»")
    log_access(request, AccessLog.Action.DELETE, other)
    other.delete()
    messages.success(request, "Cuentas fusionadas.")
    return redirect("company", pk=company.pk)


def contact_list(request):
    flt = ContactFilter(request.GET, queryset=visible(Contact, request.user).select_related("company"))
    page = Paginator(flt.qs, 50).get_page(request.GET.get("page"))
    template = "crm/_contact_rows.html" if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted") else "crm/contacts.html"
    return render(request, template, {"filter": flt, "page": page})


@require_POST
def contact_erase(request, pk):
    """RGPD: derecho de supresión."""
    contact = get_object_or_404(visible(Contact, request.user), pk=pk)
    if not (request.user.can_delete and request.user.can_edit(contact)):
        raise PermissionDenied
    log_access(request, AccessLog.Action.ERASE, contact)
    contact.erase()
    messages.success(request, "Datos personales suprimidos. Queda constancia en el registro de accesos.")
    if request.headers.get("HX-Request"):
        return refresh()
    return redirect("company", pk=contact.company_id) if contact.company_id else redirect("contacts")


# ------------------------------------------------------ Actividad y tareas
@require_POST
def activity_add(request):
    """Registro rápido desde la ficha: nota, llamada, correo o reunión."""
    company = get_object_or_404(visible(Company, request.user), pk=request.POST.get("company"))
    opp = visible(Opportunity, request.user).filter(pk=request.POST.get("opportunity") or 0, company=company).first()
    body = request.POST.get("body", "").strip()
    kind = request.POST.get("kind", "note")
    if kind not in ("note", "call", "email", "meeting"):
        kind = "note"
    if not body:
        return HttpResponse(status=204)
    activity = Activity.objects.create(
        company=company, opportunity=opp, user=request.user, kind=kind, body=body,
        subject=request.POST.get("subject", "").strip()[:250],
        direction=request.POST.get("direction", "") if request.POST.get("direction") in ("in", "out") else "",
    )
    Company.objects.filter(pk=company.pk).update(updated_at=timezone.now())
    return render(request, "crm/_activity.html", {"a": activity, "show_opp": not opp})


def _parse_when(post):
    """Fecha de la próxima acción: atajo (hoy, mañana, +3 días, +1 semana) o fecha exacta."""
    day = timezone.localdate()
    quick = post.get("quick")
    if post.get("date"):
        try:
            day = datetime.strptime(post["date"], "%Y-%m-%d").date()
        except ValueError:
            pass
    elif quick and quick.isdigit():
        day += timedelta(days=int(quick))
        while quick != "0" and day.weekday() >= 5:  # no caer en fin de semana
            day += timedelta(days=1)
    try:
        hour = datetime.strptime(post.get("time") or "09:00", "%H:%M").time()
    except ValueError:
        hour = time(9, 0)
    return timezone.make_aware(datetime.combine(day, hour))


@require_POST
def task_quick(request):
    """Alta rápida de próxima acción desde la ficha o el tablero."""
    title = request.POST.get("title", "").strip()
    opp = visible(Opportunity, request.user).filter(pk=request.POST.get("opportunity") or 0).first()
    company = opp.company if opp else visible(Company, request.user).filter(pk=request.POST.get("company") or 0).first()
    if not title:
        return refresh()
    assignee = User.objects.filter(pk=request.POST.get("assignee") or 0, is_active=True).first() or request.user
    kind = request.POST.get("kind") if request.POST.get("kind") in Task.Kind.values else Task.Kind.TODO
    Task.objects.create(title=title[:200], kind=kind, due_at=_parse_when(request.POST), assignee=assignee,
                        created_by=request.user, opportunity=opp, company=company)
    return refresh()


@require_POST
def task_toggle(request, pk):
    task = get_object_or_404(visible(Task, request.user), pk=pk)
    if task.done_at:
        task.done_at = None
        task.save()
    else:
        task.complete(request.user)
    # al cerrar la última acción de una oportunidad abierta se pide la siguiente
    opp = task.opportunity
    if opp and task.done_at and opp.stage.is_open and opp.next_action_at is None and request.POST.get("ask_next") != "0":
        return HttpResponse(status=204, headers={"HX-Trigger": '{"openModal": "%s?next=1"}' % reverse("task_next", args=[opp.pk])})
    return refresh()


def task_next(request, pk):
    opp = get_object_or_404(visible(Opportunity, request.user).select_related("company"), pk=pk)
    return render(request, "crm/_next_action.html", {"opp": opp, "users": User.objects.filter(is_active=True), "after_done": request.GET.get("next")})


def task_list(request):
    params = request.GET.copy()
    if "assignee" not in params:
        params["assignee"] = str(request.user.pk)
    base = visible(Task, request.user).select_related("assignee", "opportunity", "company")
    flt = TaskFilter(params, queryset=base)
    tasks = flt.qs if params.get("when") else flt.qs.filter(done_at__isnull=True)
    return render(request, "crm/tasks.html", {"filter": flt, "tasks": tasks[:300], "when": params.get("when", ""), "now": timezone.now(),
                                               "users": User.objects.filter(is_active=True), "assignee": params.get("assignee")})


# ------------------------------------------- Productos, suscripciones, horas
def product_list(request):
    products = Product.objects.select_related("family")
    if not request.user.can_manage_catalog:
        products = products.filter(status=Product.Status.ACTIVE)
    q = request.GET.get("q", "").strip()
    if q:
        products = products.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(description__icontains=q))
    return render(request, "crm/products.html", {"products": products, "families": ProductFamily.objects.all(), "q": q})


def subscription_list(request):
    subs = scope(Subscription.objects.all(), request.user).select_related("company", "product")
    banks = scope(HourBank.objects.all(), request.user).select_related("company", "product").prefetch_related("entries")
    active = [s for s in subs if s.status == Subscription.Status.ACTIVE]
    return render(request, "crm/subscriptions.html", {
        "subs": subs, "banks": banks, "mrr": sum(s.mrr for s in active), "active_count": len(active),
    })


def subscription_change(request, pk):
    sub = get_object_or_404(scope(Subscription.objects.all(), request.user), pk=pk)
    editable_or_403(request.user, sub.company)
    form = forms.SubscriptionChangeForm(request.POST or None, initial={"product": sub.product, "quantity": sub.quantity, "unit_price": sub.unit_price})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        event = sub.change(request.user, data["product"], data["quantity"], data["unit_price"], data["note"])
        emit("subscription.changed", {"id": sub.pk, "kind": event.kind, "company": sub.company_id, "mrr_delta": str(event.mrr_delta)})
        return refresh()
    return render(request, "crm/_modal_form.html", {
        "form": form, "title": f"Cambiar plan · {sub.company}", "action": request.path,
        "hint": "El cambio queda guardado como evento para medir expansión y contracción.", "submit": "Aplicar cambio",
    })


@require_POST
def subscription_cancel(request, pk):
    sub = get_object_or_404(scope(Subscription.objects.all(), request.user), pk=pk)
    editable_or_403(request.user, sub.company)
    sub.cancel(request.user, request.POST.get("note", ""))
    emit("subscription.changed", {"id": sub.pk, "kind": "cancel", "company": sub.company_id})
    return redirect(request.META.get("HTTP_REFERER") or "subscriptions")


# ---------------------------------------------------------------- Informes
@superuser_required
def report_view(request):
    return render(request, "crm/reports.html", reports.summary())


# ------------------------------------------------------------ Asistente IA
@require_POST
def ai_extract(request, kind):
    """Rellena una ficha a partir de texto pegado (firma de correo, tarjeta…)."""
    if kind not in ai.EXTRACT_FIELDS:
        raise Http404
    text = request.POST.get("text", "").strip()
    if not text:
        return JsonResponse({"error": "Pega primero el texto."}, status=400)
    try:
        return JsonResponse({"fields": ai.extract(kind, text)})
    except ai.AIError as exc:
        return JsonResponse({"error": str(exc)}, status=502)


@require_POST
def ai_search(request):
    """Búsqueda en lenguaje natural: la IA la traduce a filtros del pipeline."""
    question = request.POST.get("question", "").strip()
    if not question:
        return redirect("pipeline")
    options = {
        "etapas": list(Stage.objects.values("id", "name")),
        "personas": list(User.objects.filter(is_active=True).values("id", "name")),
        "productos": list(Product.objects.exclude(status=Product.Status.RETIRED).values("id", "name")),
        "familias": list(ProductFamily.objects.values("id", "name")),
    }
    try:
        filters = ai.search_filters(question, options, timezone.localdate())
    except ai.AIError as exc:
        messages.error(request, str(exc))
        return redirect("pipeline")
    from django.http import QueryDict
    query = QueryDict(mutable=True)
    query.update(filters)
    if not OpportunityFilter(query, queryset=Opportunity.objects.none()).form.is_valid():
        query = QueryDict(mutable=True)
        query["q"] = question
    query.setdefault("assignee", "")  # la búsqueda con IA mira todo el equipo salvo que nombre a alguien
    return redirect(f"{reverse('pipeline')}?{query.urlencode()}")


# ----------------------------------------------------------------- Ajustes
def settings_view(request):
    user = request.user
    context = {"api_keys": user.api_keys.filter(revoked_at__isnull=True), "new_key": request.session.pop("new_api_key", None),
               "apk_version": apk_version(), "mcp_tools": mcp.tools_for(user), "mcp_url": settings.SITE_URL.rstrip("/") + "/mcp/",
               "connected_apps": oauth.connected_apps(user), "ms_config": MicrosoftConfig.load(), "ms_redirect": microsoft.redirect_uri(),
               "ms_people": User.objects.filter(is_active=True).order_by("name") if user.is_superuser else None,
               "ms_accounts": MicrosoftAccount.objects.select_related("owner") if user.is_superuser else MicrosoftAccount.usable_by(user).select_related("owner")}
    if user.can_manage_pipeline:
        context.update(stages=Stage.objects.all(), loss_reasons=LossReason.objects.all(), templates=QuoteTemplate.objects.all())
    if user.is_superuser:
        context.update(
            users=User.objects.select_related("role"), roles=Role.objects.all(), webhooks=Webhook.objects.all(),
            deliveries=WebhookDelivery.objects.select_related("webhook")[:15],
            access_logs=AccessLog.objects.select_related("user")[:60],
        )
    return render(request, "crm/settings.html", context)


@require_POST
def api_key_create(request):
    if not request.user.can_use_api:
        raise PermissionDenied
    _, raw = ApiKey.issue(request.user, request.POST.get("name", "").strip()[:80] or "Clave")
    request.session["new_api_key"] = raw  # se muestra una sola vez
    return redirect("settings")


@require_POST
def api_key_revoke(request, pk):
    ApiKey.objects.filter(pk=pk, user=request.user).update(revoked_at=timezone.now())
    return redirect("settings")


def import_view(request):
    if not request.user.edits_all:
        raise PermissionDenied
    result = None
    if request.method == "POST" and request.FILES.get("file"):
        result = import_csv(request.FILES["file"], request.user, dry_run=request.POST.get("mode") != "import")
        if not result["dry_run"]:
            log_access(request, AccessLog.Action.CREATE, label="import_csv")
    return render(request, "crm/import.html", {"result": result})


def export_view(request):
    if not request.user.can_export:
        raise PermissionDenied
    log_access(request, AccessLog.Action.EXPORT, label="export_all")
    response = HttpResponse(export_zip(request.user), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="qopencrm-{timezone.localdate():%Y%m%d}.zip"'
    return response


# ------------------------------------------------------------------ Manual
def manual_view(request, code=None):
    """Manual de usuario adaptado al rol. Cada persona ve el suyo; el superusuario puede ver todos."""
    user = request.user
    own = manual.profile_for_user(user)
    if code and code != own["code"] and not user.is_superuser:
        raise PermissionDenied
    profile = own
    if code and code != own["code"]:
        profile = manual.SUPERUSER if code == manual.SUPERUSER["code"] else manual.profile_for_role(get_object_or_404(Role, code=code))
    tabs = [manual.profile_for_role(role) for role in Role.objects.all()] + [manual.SUPERUSER] if user.is_superuser else []
    return render(request, "crm/manual.html", {
        "p": profile, "tabs": tabs, "is_own": profile["code"] == own["code"], "shots": manual.shots(profile),
        "capture_mailbox": settings.MAIL_CAPTURE_USER if settings.MAIL_CAPTURE_HOST else "", "ai_enabled": ai.enabled(),
        "site_host": settings.SITE_URL.split("://")[-1].rstrip("/"), "apk_version": apk_version(),
        "mcp_count": len(mcp.TOOLS),
        "wide_roles": list(Role.objects.filter(view_scope="all").values_list("name", flat=True)),
        "narrow_roles": list(Role.objects.filter(view_scope="own").values_list("name", flat=True)),
    })


# -------------------------------------------------- Microsoft 365 / Teams
@superuser_required
@require_POST
def microsoft_config(request):
    """Datos de la aplicación de Microsoft Entra. Solo superusuario; el secreto se guarda cifrado."""
    form = forms.MicrosoftConfigForm(request.POST)
    if form.is_valid():
        config = MicrosoftConfig.load()
        config.tenant_id, config.client_id = form.cleaned_data["tenant_id"].strip(), form.cleaned_data["client_id"].strip()
        if form.cleaned_data["client_secret"]:
            config.client_secret_enc = microsoft.encrypt(form.cleaned_data["client_secret"].strip())
        config.save()
        messages.success(request, "Aplicación de Microsoft 365 guardada." if config.ready else "Guardado, pero falta el secreto de cliente.")
    else:
        messages.error(request, "Revisa los datos de la aplicación de Microsoft 365.")
    return redirect(reverse("settings") + "#teams")


def microsoft_connect(request):
    """Lleva a la persona a Microsoft para que inicie sesión ella misma y dé permiso. El CRM no ve su contraseña."""
    if not MicrosoftConfig.load().ready:
        messages.error(request, "Antes hay que configurar la aplicación de Microsoft 365 (superusuario, en Ajustes).")
        return redirect(reverse("settings") + "#teams")
    state = secrets.token_urlsafe(24)
    request.session["ms_oauth"] = {"state": state, "shared": request.GET.get("shared") == "1" and request.user.is_superuser}
    return redirect(microsoft.authorize_url(state))


def microsoft_callback(request):
    pending = request.session.pop("ms_oauth", None)
    if request.GET.get("error"):
        messages.error(request, f"Microsoft no ha concedido el acceso: {request.GET.get('error_description', request.GET['error'])[:200]}")
    elif not pending or not secrets.compare_digest(pending["state"], request.GET.get("state", "")) or not request.GET.get("code"):
        messages.error(request, "La conexión con Microsoft no se ha completado. Inténtalo de nuevo desde Ajustes.")
    else:
        try:
            account = microsoft.connect(request.user, request.GET["code"], shared=pending["shared"])
        except microsoft.MicrosoftError as exc:
            messages.error(request, str(exc))
        else:
            log_access(request, AccessLog.Action.CREATE, label="microsoft_account")
            messages.success(request, f"Cuenta {account.email} conectada{' y compartida con el equipo' if account.shared else ''}.")
    return redirect(reverse("settings") + "#teams")


@superuser_required
@require_POST
def microsoft_mailbox(request):
    """Da de alta un buzón de Microsoft 365 para convocar con los permisos de la aplicación (sin inicio de sesión)."""
    email = request.POST.get("email", "").strip().lower()
    owner = User.objects.filter(pk=request.POST.get("owner") or 0, is_active=True).first()
    try:
        account = microsoft.add_mailbox(owner or request.user, email, shared=owner is None)
    except microsoft.MicrosoftError as exc:
        messages.error(request, f"No se ha podido añadir {email}: {exc}")
    else:
        log_access(request, AccessLog.Action.CREATE, label="microsoft_account")
        messages.success(request, f"Buzón {account.email} añadido{' y compartido con el equipo' if account.shared else f' para {account.owner}'}.")
    return redirect(reverse("settings") + "#teams")


@require_POST
def microsoft_disconnect(request, pk):
    accounts = MicrosoftAccount.objects.all() if request.user.is_superuser else MicrosoftAccount.objects.filter(owner=request.user)
    accounts.filter(pk=pk).delete()
    return redirect(reverse("settings") + "#teams")


def teams_meeting(request, pk):
    """Convoca una reunión de Teams para una oportunidad: evento en el calendario de la cuenta, historial y próxima acción."""
    opp = get_object_or_404(visible(Opportunity, request.user).select_related("company", "contact"), pk=pk)
    editable_or_403(request.user, opp)
    accounts = MicrosoftAccount.usable_by(request.user)
    if not accounts.exists():
        return render(request, "crm/_modal_form.html", {
            "form": None, "title": "Reunión de Teams", "action": request.path, "no_submit": True,
            "hint": "No hay ninguna cuenta de Microsoft 365 conectada. Conecta la tuya en Ajustes → Reuniones de Teams, o pide que compartan la de la empresa."})
    contact = opp.contact
    start = (timezone.localtime() + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    form = forms.TeamsMeetingForm(request.POST or None, accounts=accounts, initial={
        "account": accounts.first(), "subject": f"{opp.title} · {opp.company.name}", "start": start, "minutes": 60,
        "attendees": contact.email if contact and contact.email else ""})
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            meeting = microsoft.create_meeting(data["account"], data["subject"], data["start"], data["minutes"], data["attendees"], data["message"])
        except microsoft.MicrosoftError as exc:
            form.add_error(None, str(exc))
        else:
            Activity.objects.create(
                company=opp.company, opportunity=opp, contact=contact, user=request.user, kind=Activity.Kind.MEETING, occurred_at=data["start"],
                subject=f"Reunión de Teams convocada: {data['subject']}"[:250], source="teams", external_id=meeting["id"][:255],
                body=f"Convocada desde {data['account'].email} para {', '.join(data['attendees'])}.\nEnlace: {meeting['join_url']}" + (f"\n\n{data['message']}" if data["message"] else ""))
            Task.objects.create(title=f"Reunión de Teams: {data['subject']}"[:200], kind=Task.Kind.MEETING, due_at=data["start"], assignee=request.user,
                                created_by=request.user, opportunity=opp, company=opp.company, notes=meeting["join_url"])
            log_access(request, AccessLog.Action.CREATE, opp, label="teams_meeting")
            messages.success(request, "Reunión de Teams convocada. Los asistentes reciben la invitación de Microsoft.")
            return refresh()
    return render(request, "crm/_modal_form.html", {
        "form": form, "title": "Reunión de Teams", "action": request.path, "submit": "Convocar", "wide": True,
        "hint": "Crea el evento con enlace de Teams en el calendario de la cuenta elegida. Microsoft envía la convocatoria a los asistentes; "
                "en el CRM queda anotada en el historial y como próxima acción."})


# ------------------------------------------------------------ App Android
APK_PATH = Path(settings.BASE_DIR) / "android" / "dist" / "qopencrm.apk"


def apk_version():
    version = APK_PATH.with_name("VERSION")
    return version.read_text().strip() if APK_PATH.exists() and version.exists() else ""


def apk_download(request):
    """El instalador solo se entrega con la sesión iniciada: no es un fichero público."""
    if not APK_PATH.exists():
        raise Http404
    log_access(request, AccessLog.Action.EXPORT, label="apk_download")
    return FileResponse(APK_PATH.open("rb"), as_attachment=True, filename=f"QopenCRM-{apk_version() or 'android'}.apk",
                        content_type="application/vnd.android.package-archive")


# ------------------------------------------------------------- Buscadores
def lookup(request, kind):
    """Resultados para los desplegables con buscador de empresa y contacto."""
    q = request.GET.get("q", "").strip()
    if kind == "company":
        found = visible(Company, request.user)
        if q:
            found = found.filter(Q(name__icontains=q) | Q(tax_id__icontains=q.replace(" ", "")) | Q(domain__icontains=q))
        results = [{"id": c.pk, "label": c.name, "sub": " · ".join(part for part in (c.city, c.tax_id) if part)} for c in found.order_by("name")[:12]]
    elif kind == "contact":
        found = visible(Contact, request.user).filter(erased_at__isnull=True).select_related("company")
        if request.GET.get("company", "").isdigit():
            found = found.filter(company_id=request.GET["company"])
        for word in q.split():
            found = found.filter(Q(first_name__icontains=word) | Q(last_name__icontains=word) | Q(email__icontains=word) | Q(company__name__icontains=word))
        results = [{"id": c.pk, "label": str(c), "sub": " · ".join(part for part in (c.company.name if c.company_id else "", c.email) if part),
                    "company": {"id": c.company_id, "label": c.company.name} if c.company_id else None}
                   for c in found.order_by("first_name", "last_name")[:12]]
    else:
        raise Http404
    return JsonResponse({"results": results})


# ------------------------------------------------- Formularios en diálogo
def _record(user, obj):
    return user.can_edit(obj)


MODALS = {
    # clave: (formulario, etiqueta, quién puede crear, quién puede editar)
    "company": (forms.CompanyForm, "empresa", lambda u: True, _record),
    "contact": (forms.ContactForm, "contacto", lambda u: True, _record),
    "opportunity": (forms.OpportunityForm, "oportunidad", lambda u: True, _record),
    "line": (forms.LineForm, "línea", lambda u: True, lambda u, o: u.can_edit(o.opportunity)),
    "task": (forms.TaskForm, "tarea", lambda u: True, lambda u, o: u.can_edit(o) or o.created_by_id == u.pk),
    "activity": (forms.ActivityForm, "actividad", lambda u: True, _record),
    "product": (forms.ProductForm, "producto", lambda u: u.can_manage_catalog, lambda u, o: u.can_manage_catalog),
    "family": (forms.FamilyForm, "familia", lambda u: u.can_manage_catalog, lambda u, o: u.can_manage_catalog),
    "stage": (forms.StageForm, "etapa", lambda u: u.can_manage_pipeline, lambda u, o: u.can_manage_pipeline),
    "lossreason": (forms.LossReasonForm, "motivo de pérdida", lambda u: u.can_manage_pipeline, lambda u, o: u.can_manage_pipeline),
    "quotetemplate": (forms.QuoteTemplateForm, "plantilla", lambda u: u.can_manage_pipeline, lambda u, o: u.can_manage_pipeline),
    "subscription": (forms.SubscriptionForm, "suscripción", lambda u: u.edits_all, lambda u, o: u.edits_all),
    "hourbank": (forms.HourBankForm, "bolsa de horas", lambda u: u.edits_all, lambda u, o: u.edits_all),
    "hourentry": (forms.HourEntryForm, "consumo de horas", lambda u: True, lambda u, o: u.edits_all or o.user_id == u.pk),
    "user": (forms.UserForm, "usuario", lambda u: u.is_superuser, lambda u, o: u.is_superuser),
    "role": (forms.RoleForm, "rol", lambda u: False, lambda u, o: u.is_superuser),
    "webhook": (forms.WebhookForm, "webhook", lambda u: u.is_superuser, lambda u, o: u.is_superuser),
    # incentivos: los gestionan Responsable Comercial, Director y CEO; el equipo solo consulta los suyos
    "incentive_sales": (incentive_forms.SalesIncentiveForm, "incentivo de ventas", lambda u: u.can_manage_incentives, lambda u, o: u.can_manage_incentives),
    "incentive_quota": (incentive_forms.QuotaIncentiveForm, "incentivo por cuota recurrente", lambda u: u.can_manage_incentives, lambda u, o: u.can_manage_incentives),
    "incentive_pm": (incentive_forms.PmIncentiveForm, "incentivo de PM", lambda u: u.can_manage_incentives, lambda u, o: u.can_manage_incentives),
    "prodyear": (incentive_forms.ProductivityYearForm, "ejercicio de productividad", lambda u: u.can_manage_incentives, lambda u, o: u.can_manage_incentives),
    "prodscore": (incentive_forms.ProductivityScoreForm, "nota de productividad", lambda u: u.can_manage_incentives, lambda u, o: u.can_manage_incentives),
    # ferias y eventos: solo los roles que los gestionan (por defecto, Responsable Comercial)
    "event": (forms.EventForm, "feria o evento", lambda u: u.can_manage_events, lambda u, o: u.can_manage_events),
}
# registros de negocio: borrar exige además el permiso de borrado del rol
RECORDS = {"company", "contact", "opportunity", "subscription", "hourbank"}
FEMININE = {"company", "opportunity", "line", "task", "activity", "family", "stage", "quotetemplate", "subscription", "hourbank", "event"}
PREFILL = ("company", "opportunity", "contact", "stage", "bank", "kind", "family", "event", "user", "year")


def _modal_object(request, kind, pk):
    form_class = MODALS[kind][0]
    model = form_class._meta.model
    return get_object_or_404(scope(model.objects.all(), request.user), pk=pk)


def modal_form(request, kind, pk=None):
    if kind not in MODALS:
        raise Http404
    form_class, label, can_create, can_edit = MODALS[kind]
    user = request.user
    instance = _modal_object(request, kind, pk) if pk else None
    if not (can_edit(user, instance) if instance else can_create(user)):
        raise PermissionDenied
    initial = {}
    if not instance:
        initial = {k: request.GET[k] for k in PREFILL if request.GET.get(k)}
        if kind in ("company", "contact", "opportunity"):
            initial.setdefault("owner", user.pk)
        if kind == "opportunity":
            initial.setdefault("stage", Stage.objects.filter(is_won=False, is_lost=False).values_list("pk", flat=True).first())
        if kind == "task":
            initial.setdefault("assignee", user.pk)
            initial.setdefault("due_at", (timezone.localtime() + timedelta(days=1)).replace(hour=9, minute=0))
    form = form_class(request.POST or None, instance=instance, initial=initial, user=user)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        # la línea y el consumo cuelgan de un padre que llega por la URL
        if kind == "line" and not instance:
            obj.opportunity = get_object_or_404(scope(Opportunity.objects.all(), user), pk=request.GET.get("opportunity"))
            editable_or_403(user, obj.opportunity)
        if kind == "hourentry" and not instance:
            obj.bank = get_object_or_404(scope(HourBank.objects.all(), user), pk=request.GET.get("bank"))
            obj.user = user
        if kind == "activity" and not instance:
            obj.company = get_object_or_404(scope(Company.objects.all(), user), pk=request.GET.get("company"))
            obj.user = user
        if kind in ("task", "event") and not instance:
            obj.created_by = user
        if kind.startswith("incentive_") and not instance:
            obj.created_by = user
            if obj.status != obj.Status.PROPOSED:
                obj.decided_by, obj.decided_at = user, timezone.now()
        if kind == "user" and not instance:
            obj.set_unusable_password()
        if kind == "user":
            obj.is_staff = obj.is_superuser
        obj.save()
        form.save_m2m()
        log_access(request, AccessLog.Action.UPDATE if instance else AccessLog.Action.CREATE, obj)
        for created, event in ((getattr(form, "created_company", None), "company"), (getattr(form, "created_contact", None), "contact")):
            if created:  # empresa o contacto dados de alta desde el propio diálogo de oportunidad o de contacto
                log_access(request, AccessLog.Action.CREATE, created)
                emit(f"{event}.created", {"id": created.pk, "name": str(created)})
        if kind in ("company", "contact", "opportunity"):
            emit(f"{kind}.{'updated' if instance else 'created'}", {"id": obj.pk, "name": str(obj)})
        if not instance and kind in ("company", "opportunity", "event"):
            return go(reverse(kind, args=[obj.pk]))
        return refresh()
    line_prices = None
    if kind == "line":
        line_prices = {str(p.pk): {"price": None if p.price is None else str(p.price), "annual": None if p.price_annual is None else str(p.price_annual),
                                   "unit": p.unit, "billing": p.get_billing_model_display(), "model": p.billing_model}
                       for p in Product.objects.all()}
    can_remove = bool(instance) and kind != "role" and (user.can_delete if kind in RECORDS else True) and not (kind == "user" and instance == user)
    return render(request, "crm/_modal_form.html", {
        "form": form, "action": request.get_full_path(), "line_prices": line_prices,
        "title": f"{'Editar' if instance else 'Nueva' if kind in FEMININE else 'Nuevo'} {label}",
        "delete_url": reverse("modal_delete", args=[kind, pk]) if can_remove else None,
        "erase_url": reverse("contact_erase", args=[pk]) if can_remove and kind == "contact" and not instance.erased_at else None,
        "wide": kind in ("company", "contact", "opportunity", "product", "quotetemplate", "incentive_sales", "incentive_quota", "incentive_pm", "prodyear"),
        "ai_kind": kind if kind in ai.EXTRACT_FIELDS and ai.enabled() else None,
    })


@require_POST
def modal_delete(request, kind, pk):
    if kind not in MODALS or kind == "role":
        raise Http404
    user = request.user
    obj = _modal_object(request, kind, pk)
    if not MODALS[kind][3](user, obj) or (kind in RECORDS and not user.can_delete) or obj == user:
        raise PermissionDenied
    log_access(request, AccessLog.Action.DELETE, obj)
    target = None
    if kind == "opportunity":
        target = reverse("pipeline")
    elif kind == "company":
        target = reverse("companies")
    elif kind == "event":
        target = reverse("events")
    try:
        obj.delete()
    except Exception:  # ProtectedError: tiene registros que dependen de él
        return HttpResponse("No se puede borrar: hay registros que dependen de este elemento.", status=409)
    return go(target) if target else refresh()
