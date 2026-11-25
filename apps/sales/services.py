"""Lógica comercial: ganar una oportunidad, generar presupuestos versionados."""
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

from apps.catalog.models import BillingModel
from apps.crm.models import Activity, Company, OpportunityLine
from apps.integrations.webhooks import emit

from .models import ISSUER_ADDRESS, ISSUER_FOOTER, ISSUER_NAME, HourBank, Quote, QuoteTemplate, Subscription, SubscriptionEvent


def _next_renewal(start, period):
    try:
        if period == OpportunityLine.Period.ANNUAL:
            return start.replace(year=start.year + 1)
        month = start.month % 12 + 1
        return start.replace(year=start.year + (start.month == 12), month=month)
    except ValueError:  # 29-31 en un mes más corto
        return start + timedelta(days=365 if period == OpportunityLine.Period.ANNUAL else 30)


@transaction.atomic
def fulfil_won(opportunity, user=None):
    """Al ganar: cada línea recurrente pasa a suscripción y cada línea por horas a bolsa de horas."""
    if opportunity.subscriptions.exists() or opportunity.hour_banks.exists():
        return
    from apps.incentives.services import propose_for

    was_customer = opportunity.company.kind == Company.Kind.CUSTOMER
    today = timezone.localdate()
    for line in opportunity.lines.select_related("product"):
        if line.billing_model == BillingModel.MONTHLY:
            sub = Subscription.objects.create(
                company=opportunity.company, product=line.product, label="" if line.product_id else line.description, opportunity=opportunity,
                quantity=line.quantity, unit_price=line.unit_price or 0, discount=line.discount,
                period=line.period, started_on=today, renews_on=_next_renewal(today, line.period),
            )
            sub.record(SubscriptionEvent.Kind.NEW, user)
            emit("subscription.changed", {"id": sub.pk, "kind": "new", "company": sub.company_id, "product": line.product.code if line.product_id else "", "mrr": str(sub.mrr)})
        elif line.billing_model == BillingModel.HOURLY:
            HourBank.objects.create(
                company=opportunity.company, product=line.product, opportunity=opportunity,
                name=line.description or str(line.product), hours_sold=line.quantity, hourly_rate=line.unit_price,
            )
    if opportunity.company.kind != Company.Kind.CUSTOMER:
        opportunity.company.kind = Company.Kind.CUSTOMER
        opportunity.company.save(update_fields=["kind", "updated_at"])
    propose_for(opportunity, was_customer)


def _plain(number):
    """Decimal sin ceros de relleno: 1, 2.5, 12."""
    return str(number.to_integral()) if number == number.to_integral() else str(number.normalize())


def _line_snapshot(line):
    return {
        "code": line.product.code if line.product_id else "",
        "description": line.description,
        "billing_model": line.billing_model,
        "period": line.period,
        "quantity": _plain(line.quantity),
        "unit": line.unit,
        "unit_price": None if line.unit_price is None else str(line.unit_price),
        "discount": str(line.discount),
        "total": str(line.total),
    }


def _fill(text, opportunity):
    contact = opportunity.contact.full_name if opportunity.contact_id else ""
    for key, value in {"cuenta": opportunity.company.name, "empresa": opportunity.company.name, "contacto": contact, "oportunidad": opportunity.title}.items():
        text = text.replace("{{" + key + "}}", value).replace("{{ " + key + " }}", value)
    return text


def default_bill_to(company):
    place = ", ".join(part for part in (company.city, company.province, company.country) if part)
    return "\n".join(part for part in (company.name, company.address, place, company.tax_id) if part)


def to_decimal(value, default=None):
    """Número escrito a la española o a la inglesa: «1.234,50», «1234.5»."""
    text = str(value if value is not None else "").strip().replace("€", "").replace(" ", "")
    if not text:
        return default
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError(f"«{value}» no es un número.")


def clean_lines(rows):
    """Normaliza las líneas editadas a mano y recalcula sus importes."""
    lines = []
    for row in rows:
        description = (row.get("description") or "").strip()
        if not description:
            continue
        billing = row.get("billing_model") if row.get("billing_model") in BillingModel.values else BillingModel.ONE_OFF
        period = row.get("period") if row.get("period") in OpportunityLine.Period.values else OpportunityLine.Period.MONTHLY
        quantity = to_decimal(row.get("quantity"), Decimal(1))
        price = to_decimal(row.get("unit_price"))
        discount = to_decimal(row.get("discount"), Decimal(0))
        if quantity < 0 or (price is not None and price < 0) or not 0 <= discount <= 100:
            raise ValueError(f"Revisa cantidad, precio y descuento de «{description.splitlines()[0][:60]}».")
        total = Decimal(0) if price is None else (quantity * price * (100 - discount) / 100).quantize(Decimal("0.01"))
        lines.append({
            "code": (row.get("code") or "").strip(), "description": description, "billing_model": billing, "period": period,
            "quantity": _plain(quantity), "unit": (row.get("unit") or "").strip()[:30],
            "unit_price": None if price is None else str(price.quantize(Decimal("0.01"))),
            "discount": str(discount.quantize(Decimal("0.01"))), "total": str(total),
        })
    if not lines:
        raise ValueError("El presupuesto necesita al menos una línea.")
    return lines


def _totals(lines):
    """Recurrente (equivalente mensual), servicios por horas y pago único, por separado."""
    totals = {BillingModel.MONTHLY: Decimal(0), BillingModel.HOURLY: Decimal(0), BillingModel.ONE_OFF: Decimal(0)}
    for line in lines:
        amount = Decimal(line["total"])
        if line["billing_model"] == BillingModel.MONTHLY and line["period"] == OpportunityLine.Period.ANNUAL:
            amount = (amount / 12).quantize(Decimal("0.01"))
        totals[line["billing_model"]] += amount
    return totals


def quote_logo():
    """Logo de los presupuestos: el de la instalación (branding/logo.png) o, si no lo hay, el genérico."""
    own = Path(settings.BRANDING_DIR) / "logo.png"
    return own if own.exists() else Path(settings.BASE_DIR) / "static" / "img" / "quote" / "logo.png"


def next_number(today=None):
    today = today or timezone.localdate()
    prefix = f"{settings.QUOTE_NUMBER_PREFIX}{today:%y} "
    last = Quote.objects.filter(number__startswith=prefix).order_by("-number").first()
    return f"{prefix}{(int(last.number[len(prefix):]) + 1 if last else 1):06d}"


def draft_from_opportunity(opportunity, template=None):
    """Datos de partida de un presupuesto nuevo: lo que propone la oportunidad sobre la plantilla."""
    template = template or QuoteTemplate.objects.first()
    today = timezone.localdate()
    return {
        "number": next_number(today), "title": opportunity.title, "bill_to": default_bill_to(opportunity.company), "issued_on": today,
        "valid_until": today + timedelta(days=template.validity_days if template else 30),
        "intro": _fill(template.intro, opportunity) if template else "", "terms": template.terms if template else "",
        "tax_rate": template.tax_rate if template else Decimal(21),
        "lines": [_line_snapshot(line) for line in opportunity.lines.select_related("product")],
    }


@transaction.atomic
def save_quote(opportunity, user, template, data, previous=None):
    """Guarda una versión nueva. Nunca modifica una existente: la anterior queda tal cual, con su PDF."""
    if previous:
        number = previous.number
        version = Quote.objects.filter(number=number).order_by("-version").values_list("version", flat=True).first() + 1
    else:
        number, version = (data.get("number") or "").strip() or next_number(), 1
        if Quote.objects.filter(number=number).exists():
            raise ValueError(f"Ya existe un presupuesto con el número {number}.")
    lines = data["lines"]
    totals = _totals(lines)
    quote = Quote.objects.create(
        opportunity=opportunity, template=template, number=number, version=version, created_by=user,
        title=data.get("title", "")[:200], bill_to=data.get("bill_to", ""), issued_on=data["issued_on"], valid_until=data.get("valid_until"),
        intro=data.get("intro", ""), terms=data.get("terms", ""), tax_rate=data["tax_rate"], lines=lines,
        total_mrr=totals[BillingModel.MONTHLY], total_services=totals[BillingModel.HOURLY], total_one_off=totals[BillingModel.ONE_OFF],
    )
    quote.pdf.save(f"{number.replace(' ', '-')}-v{version}.pdf", ContentFile(render_quote_pdf(quote)), save=True)
    Activity.objects.create(
        company=opportunity.company, opportunity=opportunity, user=user, kind=Activity.Kind.SYSTEM,
        subject=f"Presupuesto {quote} generado",
    )
    emit("quote.created", {"id": quote.pk, "number": number, "version": version, "opportunity": opportunity.pk})
    return quote


def create_quote(opportunity, user=None, template=None):
    """Presupuesto directo desde las líneas de la oportunidad; si ya tiene uno, es su siguiente versión."""
    template = template or QuoteTemplate.objects.first()
    opportunity.recalc()
    previous = opportunity.quotes.order_by("-version").first()
    data = draft_from_opportunity(opportunity, template)
    if previous:
        data.update(title=previous.title or data["title"], bill_to=previous.bill_to or data["bill_to"])
    return save_quote(opportunity, user, template, data, previous)


def send_quote(quote, user, to, cc, subject, body):
    """Envía el PDF por correo, lo marca como enviado y lo deja en el historial de la cuenta."""
    message = EmailMessage(
        subject=subject, body=body, to=to, cc=cc, reply_to=[user.email] if user and user.email else None,
    )
    name = f"Presupuesto {quote.number} v{quote.version}.pdf"
    with quote.pdf.open("rb") as handle:
        message.attach(name, handle.read(), "application/pdf")
    message.send()
    quote.status = Quote.Status.SENT if quote.status == Quote.Status.DRAFT else quote.status
    quote.sent_at, quote.sent_to = timezone.now(), ", ".join(to + cc)[:300]
    quote.save(update_fields=["status", "sent_at", "sent_to"])
    opportunity = quote.opportunity
    Activity.objects.create(
        company=opportunity.company, opportunity=opportunity, contact=opportunity.contact, user=user, kind=Activity.Kind.EMAIL,
        direction="out", subject=subject, body=f"Presupuesto {quote} enviado a {', '.join(to)}.\n\n{body}",
    )


def render_quote_pdf(quote):
    from weasyprint import HTML

    template = quote.template
    html = render_to_string("sales/quote_pdf.html", {
        "quote": quote, "opportunity": quote.opportunity, "company": quote.opportunity.company,
        "bill_to": quote.bill_to or default_bill_to(quote.opportunity.company),
        "issuer_name": template.issuer_name if template else ISSUER_NAME,
        "issuer_address": template.issuer_address if template else ISSUER_ADDRESS,
        "footer": template.footer if template else ISSUER_FOOTER,
        "logo": quote_logo(), "fonts": Path(settings.BASE_DIR) / "docker" / "fonts",
    })
    return HTML(string=html, base_url=str(settings.BASE_DIR)).write_pdf()
