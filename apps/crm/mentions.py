"""Menciones con @usuario en notas, actividades y tareas.

El identificador de cada persona es la parte de su correo antes de la arroba
(«ana.ruiz@example.com» → «@ana.ruiz»): único, sin espacios y
fácil de teclear. El buscador del texto lo sugiere al escribir «@».

«@todos» avisa de una vez a quienes llevan el asunto: responsable y asignados del lead,
y responsables de su empresa y de su contacto.
"""
import logging
import re

from django.conf import settings
from django.db import transaction

from apps.accounts import mailer
from apps.accounts.mailer import send_internal

logger = logging.getLogger(__name__)

# «@» al principio o tras un espacio o signo: así no se confunde con un correo (ana@acme.es)
MENTION_RE = re.compile(r"(?<![\w.@])@([a-zA-Z0-9][\w.\-]*[\w])", re.UNICODE)


def handle(user):
    return user.email.split("@")[0].lower()


def handles_in(text):
    return {m.lower() for m in MENTION_RE.findall(text or "")}


EVERYONE = "todos"


def involved_users(target):
    """Quienes llevan el asunto de una nota o tarea: responsable y asignados del lead, y responsables de su empresa y su contacto."""
    opportunity = target.opportunity if target.opportunity_id else None
    company = target.company if target.company_id else (opportunity.company if opportunity else None)
    contact = getattr(target, "contact", None) or (opportunity.contact if opportunity else None)
    people = [company.owner if company else None, contact.owner if contact else None]
    if opportunity:
        people += [opportunity.owner, *opportunity.assignees.all()]
    return [u for u in dict.fromkeys(people) if u is not None and u.is_active]


def mentioned_users(text, target=None):
    from apps.accounts.models import User

    wanted = handles_in(text)
    if not wanted:
        return []
    named = [u for u in User.objects.filter(is_active=True) if handle(u) in wanted]
    if EVERYONE in wanted and target is not None:
        named += [u for u in involved_users(target) if u not in named]
    return named


def notify(actor, text, *, activity=None, task=None, previous=""):
    """Avisa a quien se menciona en «text» (salvo a quien ya estaba en «previous» y al propio autor)."""
    from .models import Notification

    target = activity or task
    already = {u.pk for u in mentioned_users(previous, target)}  # al editar no se repite el aviso
    recipients = [u for u in mentioned_users(text, target) if u.pk not in already and u != actor]
    if not recipients:
        return []
    company = target.company if target.company_id else None
    opportunity = target.opportunity if target.opportunity_id else None
    where = opportunity or company or (task and task.title) or "el CRM"
    path = f"/oportunidades/{opportunity.pk}/" if opportunity else (f"/cuentas/{company.pk}/" if company else "/tareas/")
    created = [
        Notification.objects.create(recipient=u, actor=actor, activity=activity, task=task, company=company, opportunity=opportunity,
                                    text=(text or "")[:1000], url=path)
        for u in recipients
    ]
    who = str(actor) if actor else "Alguien"
    context = mailer.where(company, opportunity)
    kind = "una tarea" if task else "una nota"

    def mail():
        for u in recipients:
            try:
                send_internal(
                    f"{who} te ha mencionado" + (f" · {context}" if context else f" en {where}"),
                    f"{who} te ha mencionado en {kind} sobre {where}:\n\n{text}\n\n{settings.SITE_URL}{path}\n\n"
                    f"Tus avisos: {settings.SITE_URL}/avisos/",
                    u.email,
                )
            except Exception:  # el correo no debe tumbar el guardado
                logger.exception("No se pudo enviar el aviso de mención a %s", u.email)

    transaction.on_commit(mail)
    return created
