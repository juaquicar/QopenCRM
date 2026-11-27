"""Historial de cambios en los leads (oportunidades): qué se cambió, de qué a qué y quién.

Los cambios de etapa por el tablero ya dejan su propia entrada (Opportunity.move_to); esto cubre
lo que se edita desde la ficha o el asistente: alta, campos y líneas.
"""
from django.utils.formats import localize

from .models import Activity, Opportunity

TRACKED = ["title", "company", "contact", "stage", "owner", "assignees", "priority", "probability", "expected_close", "source", "description"]


def _show(opp, name):
    if name == "assignees":
        return ", ".join(sorted(str(u) for u in opp.assignees.all())) if opp.pk else ""
    field = Opportunity._meta.get_field(name)
    if field.choices:
        return getattr(opp, f"get_{name}_display")()
    value = getattr(opp, name)
    return "" if value is None else (localize(value) if not hasattr(value, "_meta") else str(value))


def snapshot(opp):
    return {name: _show(opp, name) for name in TRACKED}


def label(name):
    return str(Opportunity._meta.get_field(name).verbose_name).capitalize() if name != "assignees" else "Personas asignadas"


def record(user, opp, before=None):
    """Deja en el historial el alta del lead o lo que ha cambiado desde «before» (un snapshot)."""
    after = snapshot(opp)
    if before is None:
        body = "\n".join(f"{label(k)}: {after[k]}" for k in ("stage", "owner", "priority") if after[k])
        return Activity.objects.create(company=opp.company, opportunity=opp, user=user, kind=Activity.Kind.SYSTEM, subject="Lead creado", body=body)
    changed = [k for k in TRACKED if before[k] != after[k]]
    if not changed:
        return None
    lines = [f"{label(k)}: actualizada" if k == "description" else f"{label(k)}: {before[k] or '—'} → {after[k] or '—'}" for k in changed]
    return Activity.objects.create(company=opp.company, opportunity=opp, user=user, kind=Activity.Kind.SYSTEM,
                                   subject="Lead modificado: " + ", ".join(label(k).lower() for k in changed)[:230], body="\n".join(lines))


def record_line(user, line, action):
    """action: «añadida», «cambiada» o «quitada»."""
    opp = line.opportunity
    price = "a consultar" if line.unit_price is None else f"{localize(line.quantity.normalize())} × {localize(line.unit_price)} €"
    return Activity.objects.create(company=opp.company, opportunity=opp, user=user, kind=Activity.Kind.SYSTEM,
                                   subject=f"Línea {action}: {line}"[:250], body=f"{price}" + (f" · dto. {localize(line.discount.normalize())} %" if line.discount else ""))
