from decimal import Decimal, InvalidOperation

from django import template
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

DAYS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _number(value, decimals):
    try:
        value = Decimal(str(value if value not in (None, "") else 0))
    except InvalidOperation:
        return str(value)
    text = f"{value:,.{decimals}f}"
    return text.replace(",", "·").replace(".", ",").replace("·", ".")


@register.filter
def eur(value):
    """1.234,50 € — sin decimales cuando el importe es redondo."""
    try:
        whole = Decimal(str(value or 0)) == Decimal(str(value or 0)).to_integral()
    except InvalidOperation:
        whole = False
    return f"{_number(value, 0 if whole else 2)} €"


@register.filter
def num(value):
    try:
        whole = Decimal(str(value or 0)) == Decimal(str(value or 0)).to_integral()
    except InvalidOperation:
        whole = False
    return _number(value, 0 if whole else 2)


@register.filter
def due(value):
    """Fecha cercana en lenguaje llano: «hoy 10:00», «mañana», «vie 28 nov», «hace 3 días»."""
    if not value:
        return ""
    local = timezone.localtime(value) if hasattr(value, "hour") else value
    day = local.date() if hasattr(local, "date") else local
    delta = (day - timezone.localdate()).days
    hour = f" {local:%H:%M}" if hasattr(local, "hour") else ""
    if delta == 0:
        return f"hoy{hour}"
    if delta == 1:
        return f"mañana{hour}"
    if delta == -1:
        return "ayer"
    if delta < -1:
        return f"hace {-delta} días"
    if delta < 7:
        return f"{DAYS[day.weekday()]} {day.day}{hour}"
    return f"{day.day} {MONTHS[day.month - 1]}" + (f" {day.year}" if day.year != timezone.localdate().year else "")


@register.filter
def due_class(value):
    if not value:
        return "none"
    if value < timezone.now():
        return "late"
    return "soon" if (timezone.localtime(value).date() - timezone.localdate()).days <= 1 else ""


@register.filter
def short_date(value):
    if not value:
        return "—"
    day = timezone.localtime(value).date() if hasattr(value, "hour") else value
    return f"{day.day} {MONTHS[day.month - 1]}" + (f" {day.year}" if day.year != timezone.localdate().year else "")


@register.filter
def month_label(value):
    return f"{MONTHS[value.month - 1]} {value.year}"


@register.simple_tag
def icon(name):
    return format_html('<svg aria-hidden="true"><use href="#i-{}"/></svg>', name)


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Query string actual con cambios: {% qs view='list' %}."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    return mark_safe("?" + params.urlencode()) if params else "?"


@register.filter
def get(mapping, key):
    return mapping.get(key) if mapping else None


@register.simple_tag
def shot(name, caption, kind=""):
    """Captura del manual, ampliable en pestaña nueva."""
    from django.templatetags.static import static

    url = static(f"img/manual/{name}.webp")
    return format_html(
        '<figure class="shot {}"><a href="{}" target="_blank" rel="noopener" title="Ampliar"><img src="{}" alt="{}" loading="lazy"></a>'
        "<figcaption>{}</figcaption></figure>", kind, url, url, caption, caption)


@register.filter
def amount(value):
    """Importe con dos decimales siempre, sin símbolo: 2.500,00."""
    return _number(value, 2)


@register.filter
def quote_desc(value):
    """Descripción de una línea de presupuesto: las líneas que empiezan por -, * o • salen como lista."""
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    out, items = [], []

    def flush():
        if items:
            out.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for line in str(value or "").splitlines():
        text = line.strip()
        if text[:1] in "-*•" and text[1:2] in (" ", ""):
            items.append(escape(text[1:].strip()))
        elif text:
            flush()
            out.append(f"<div>{escape(text)}</div>")
    flush()
    return mark_safe("".join(out))
