"""Captura automática de correo y reuniones desde un buzón compartido (IMAP).

El equipo pone el buzón en copia (o lo reenvía) y cada mensaje que cruza con un contacto
queda en el historial de su cuenta. El buzón se abre en modo solo lectura: no se marca nada como leído.
"""
import email
import imaplib
import re
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.crm.models import GENERIC_DOMAINS, Activity, Company, Contact

from .models import MailboxState

ADDRESS = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
QUOTE_START = re.compile(
    r"^(>|-{2,}\s*(Mensaje original|Original Message|Forwarded message|Mensaje reenviado)|El .{5,80} escribió:|On .{5,80} wrote:|De: .+|From: .+)", re.I)


def _text(value):
    try:
        return str(make_header(decode_header(value or ""))).strip()
    except Exception:
        return (value or "").strip()


def _body(message):
    """Texto del mensaje (y su invitación de calendario, si la trae)."""
    plain, html, calendar = "", "", ""
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind not in ("text/plain", "text/html", "text/calendar"):
            continue
        try:
            content = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        if kind == "text/plain" and not plain:
            plain = content
        elif kind == "text/html" and not html:
            html = content
        elif kind == "text/calendar" and not calendar:
            calendar = content
    if not plain and html:
        html = re.sub(r"(?is)<(script|style|head).*?</\1>", "", html)
        html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", html)
        plain = unescape(re.sub(r"<[^>]+>", "", html))
    return plain.replace("\r\n", "\n"), calendar


def _own_text(body):
    """Lo escrito en este mensaje, sin la cadena de respuestas citadas."""
    lines = []
    for line in body.split("\n"):
        if QUOTE_START.match(line.strip()):
            break
        lines.append(line.rstrip())
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return text[:4000]


def _meeting(calendar):
    """Datos de una invitación (iCalendar): asunto y fecha de inicio."""
    if "BEGIN:VEVENT" not in calendar or re.search(r"METHOD:\s*(CANCEL|REPLY)", calendar):
        return None
    unfolded = re.sub(r"\r?\n[ \t]", "", calendar)
    summary = re.search(r"^SUMMARY[^:\n]*:(.*)$", unfolded, re.M)
    start = re.search(r"^DTSTART[^:\n]*:(\d{8})(?:T(\d{6})(Z)?)?", unfolded, re.M)
    if not start:
        return None
    moment = datetime.strptime(start.group(1) + (start.group(2) or "090000"), "%Y%m%d%H%M%S")
    moment = moment.replace(tzinfo=timezone.UTC) if start.group(3) else timezone.make_aware(moment)
    location = re.search(r"^LOCATION[^:\n]*:(.*)$", unfolded, re.M)
    clean = lambda m: m.group(1).replace("\\,", ",").replace("\\n", " ").strip() if m else ""
    return {"subject": clean(summary), "when": moment, "location": clean(location)}


class Matcher:
    """Relaciona direcciones de correo con contactos, cuentas y usuarios del CRM."""

    def __init__(self):
        self.users = {u.email.lower(): u for u in User.objects.filter(is_active=True)}
        self.own_domains = {e.split("@")[1] for e in self.users} | {settings.MAIL_CAPTURE_USER.split("@")[-1].lower()}

    def is_internal(self, address):
        return address in self.users or address.split("@")[-1] in self.own_domains

    def contact_for(self, address, name=""):
        contact = Contact.objects.filter(email=address, erased_at__isnull=True).select_related("company").first()
        if contact and contact.company_id:
            return contact
        domain = address.split("@")[-1]
        if contact or domain in GENERIC_DOMAINS:
            return None
        # persona nueva de una cuenta conocida: se da de alta sola para no perder el hilo
        company = Company.objects.filter(domain=domain).first()
        if not company:
            return None
        first, _, last = (name or address.split("@")[0]).partition(" ")
        return Contact.objects.create(company=company, first_name=first[:80] or address, last_name=last[:120], email=address,
                                      owner=company.owner, notes="Alta automática desde el correo.")


def process(raw, matcher, folder="INBOX"):
    """Convierte un mensaje en actividad. Devuelve la actividad creada o None si no cruza con nadie."""
    message = email.message_from_bytes(raw)
    message_id = (message.get("Message-ID") or "").strip()[:255]
    if not message_id:
        return None
    sender = [(n, a.lower()) for n, a in getaddresses([message.get("From", "")]) if "@" in a]
    recipients = [(n, a.lower()) for n, a in getaddresses(message.get_all("To", []) + message.get_all("Cc", [])) if "@" in a]
    if not sender:
        return None
    body, calendar = _body(message)
    outgoing = matcher.is_internal(sender[0][1])
    external = [(_text(n), a) for n, a in (recipients if outgoing else sender) if not matcher.is_internal(a)]
    if outgoing and not external:
        # reenviado al buzón por alguien del equipo: el cliente está en el cuerpo citado
        external = [("", a.lower()) for a in dict.fromkeys(ADDRESS.findall(body[:6000])) if not matcher.is_internal(a.lower())]
        outgoing = False
    contacts = [c for c in (matcher.contact_for(a, n) for n, a in external) if c]
    if not contacts:
        return None
    try:
        when = parsedate_to_datetime(message.get("Date"))
        when = when if when.tzinfo else timezone.make_aware(when)
    except (TypeError, ValueError):
        when = timezone.now()
    when = min(when, timezone.now())
    subject = _text(message.get("Subject"))[:250]
    user = matcher.users.get(sender[0][1]) or next((matcher.users[a] for _, a in recipients if a in matcher.users), None)
    meeting = _meeting(calendar) if calendar else None
    created = None
    for company_id in dict.fromkeys(c.company_id for c in contacts):
        contact = next(c for c in contacts if c.company_id == company_id)
        open_opps = list(contact.company.opportunities.filter(stage__is_won=False, stage__is_lost=False)[:2])
        fields = dict(
            company_id=company_id, contact=contact, user=user, source="mail", external_id=message_id,
            opportunity=open_opps[0] if len(open_opps) == 1 else None,
        )
        if meeting:
            fields.update(kind=Activity.Kind.MEETING, subject=(meeting["subject"] or subject)[:250], occurred_at=meeting["when"],
                          body=(f"Lugar: {meeting['location']}\n" if meeting["location"] else "") + _own_text(body))
        else:
            fields.update(kind=Activity.Kind.EMAIL, subject=subject, occurred_at=when, body=_own_text(body),
                          direction=Activity.Direction.OUT if outgoing else Activity.Direction.IN)
        try:
            with transaction.atomic():
                created = Activity.objects.create(**fields)
        except IntegrityError:  # ya capturado (mismo Message-ID en esa cuenta)
            continue
    return created


def sync(verbose=None):
    """Lee lo nuevo de cada carpeta configurada. Devuelve (leídos, registrados)."""
    if not settings.MAIL_CAPTURE_HOST:
        return 0, 0
    say = verbose or (lambda text: None)
    matcher = Matcher()
    seen = stored = 0
    connection = imaplib.IMAP4_SSL(settings.MAIL_CAPTURE_HOST, settings.MAIL_CAPTURE_PORT, timeout=30)
    try:
        connection.login(settings.MAIL_CAPTURE_USER, settings.MAIL_CAPTURE_PASSWORD)
        for folder in settings.MAIL_CAPTURE_FOLDERS:
            status, _ = connection.select(f'"{folder}"', readonly=True)
            if status != "OK":
                say(f"Carpeta no encontrada: {folder}")
                continue
            validity = int(connection.response("UIDVALIDITY")[1][0] or 0)
            state, _ = MailboxState.objects.get_or_create(folder=folder)
            if state.uid_validity != validity:
                state.uid_validity, state.last_uid = validity, 0
            if state.last_uid:
                criteria = f"UID {state.last_uid + 1}:*"
            else:  # primera vez: solo lo reciente, no todo el histórico
                since = timezone.localdate() - timedelta(days=settings.MAIL_CAPTURE_SINCE_DAYS)
                criteria = f"SINCE {since:%d}-{'Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split()[since.month - 1]}-{since:%Y}"
            status, data = connection.uid("search", None, criteria)
            uids = [int(u) for u in (data[0].split() if status == "OK" and data and data[0] else []) if int(u) > state.last_uid]
            for uid in uids:
                status, fetched = connection.uid("fetch", str(uid), "(BODY.PEEK[])")
                raw = next((item[1] for item in fetched or [] if isinstance(item, tuple)), None)
                seen += 1
                if raw:
                    try:
                        if process(raw, matcher, folder):
                            stored += 1
                    except Exception as exc:  # un mensaje raro no debe parar la captura
                        say(f"Mensaje {uid} de {folder} omitido: {exc}")
                state.last_uid = uid
            state.synced_at = timezone.now()
            state.save()
            say(f"{folder}: {len(uids)} mensajes nuevos")
    finally:
        try:
            connection.logout()
        except Exception:
            pass
    return seen, stored
