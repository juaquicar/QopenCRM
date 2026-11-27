"""Correos internos del CRM (a personas del equipo): todos llevan el mismo prefijo en el asunto.

Los que van a clientes (presupuestos) no pasan por aquí: su asunto lo escribe quien envía.
"""
from django.conf import settings
from django.core.mail import send_mail


def subject(text):
    prefix = settings.INTERNAL_EMAIL_PREFIX
    return f"{prefix} {text}" if prefix else text


def where(company=None, opportunity=None):
    """«Empresa · Oportunidad» para el asunto, con lo que haya."""
    if opportunity is not None and company is None:
        company = opportunity.company
    return " · ".join(str(part) for part in (company, opportunity) if part)


def send_internal(text, body, to):
    return send_mail(subject(text), body, None, to if isinstance(to, (list, tuple)) else [to])
