"""Reuniones de Teams con Microsoft Graph.

Microsoft no permite crear reuniones con usuario y contraseña: hace falta una aplicación registrada en Entra ID y que
cada cuenta dé su consentimiento iniciando sesión en Microsoft. Aquí está ese flujo (OAuth 2.0, código de autorización)
y la llamada que crea el evento con reunión de Teams en el calendario de la cuenta elegida. El CRM nunca ve la
contraseña: guarda, cifrado, el token de renovación que Microsoft entrega tras el consentimiento.
"""
import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.utils import timezone

from .models import MicrosoftAccount, MicrosoftConfig

SCOPES = "offline_access User.Read Calendars.ReadWrite OnlineMeetings.ReadWrite"
LOGIN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{endpoint}"
GRAPH = "https://graph.microsoft.com/v1.0"


class MicrosoftError(Exception):
    """Fallo al hablar con Microsoft, con un mensaje que se le puede enseñar a la persona."""


def _fernet():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("ms365:" + settings.SECRET_KEY).encode()).digest()))


def encrypt(text):
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token):
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        raise MicrosoftError("No se puede leer la credencial guardada: vuelve a conectar la cuenta.")


def redirect_uri():
    return settings.SITE_URL.rstrip("/") + "/ajustes/microsoft/callback/"


def _request(url, data=None, token=None, json_body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if json_body is not None:
        body, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc)
        except ValueError:
            detail = {}
        error = detail.get("error")
        message = detail.get("error_description") or (error.get("message") if isinstance(error, dict) else error)
        raise MicrosoftError(f"Microsoft ha respondido {exc.code}: {str(message or 'sin detalle').splitlines()[0][:240]}")
    except (urllib.error.URLError, TimeoutError):
        raise MicrosoftError("No se puede conectar con Microsoft. Inténtalo de nuevo en un momento.")


def authorize_url(state):
    config = MicrosoftConfig.load()
    query = urllib.parse.urlencode({
        "client_id": config.client_id, "response_type": "code", "redirect_uri": redirect_uri(), "response_mode": "query",
        "scope": SCOPES, "state": state, "prompt": "select_account"})
    return LOGIN.format(tenant=config.tenant_id, endpoint="authorize") + "?" + query


def _token(scope=SCOPES, **grant):
    config = MicrosoftConfig.load()
    if not config.ready:
        raise MicrosoftError("Falta configurar la aplicación de Microsoft 365 en Ajustes.")
    return _request(LOGIN.format(tenant=config.tenant_id, endpoint="token"), data={
        "client_id": config.client_id, "client_secret": decrypt(config.client_secret_enc), "scope": scope, **grant})


def _app_token():
    """Token de la propia aplicación (permisos de aplicación sobre los calendarios): no necesita que nadie inicie sesión."""
    return _token(scope="https://graph.microsoft.com/.default", grant_type="client_credentials")["access_token"]


def add_mailbox(owner, email, shared=False):
    """Da de alta un buzón para convocar con permisos de aplicación, comprobando antes que la aplicación llega a su calendario."""
    email = email.strip().lower()
    _request(f"{GRAPH}/users/{urllib.parse.quote(email)}/calendar?$select=name", token=_app_token())
    account, _ = MicrosoftAccount.objects.update_or_create(owner=owner, email=email, defaults={"shared": shared, "refresh_token_enc": ""})
    return account


def connect(owner, code, shared=False):
    """Canjea el código que devuelve Microsoft tras el consentimiento y guarda la cuenta."""
    tokens = _token(grant_type="authorization_code", code=code, redirect_uri=redirect_uri())
    if not tokens.get("refresh_token"):
        raise MicrosoftError("Microsoft no ha entregado permiso permanente (offline_access). Revisa los permisos de la aplicación.")
    me = _request(f"{GRAPH}/me?$select=displayName,mail,userPrincipalName", token=tokens["access_token"])
    email = (me.get("mail") or me.get("userPrincipalName") or "").lower()
    account, _ = MicrosoftAccount.objects.update_or_create(owner=owner, email=email, defaults={
        "name": me.get("displayName") or "", "shared": shared, "refresh_token_enc": encrypt(tokens["refresh_token"])})
    return account


def _access_token(account):
    tokens = _token(grant_type="refresh_token", refresh_token=decrypt(account.refresh_token_enc))
    if tokens.get("refresh_token"):  # Microsoft rota el token de renovación: se guarda el nuevo
        account.refresh_token_enc = encrypt(tokens["refresh_token"])
    account.last_used_at = timezone.now()
    account.save(update_fields=["refresh_token_enc", "last_used_at"])
    return tokens["access_token"]


def create_meeting(account, subject, start, minutes, attendees, message=""):
    """Crea el evento con reunión de Teams en el calendario de la cuenta. Microsoft envía la convocatoria a los asistentes."""
    from datetime import timedelta
    from html import escape

    local = timezone.localtime(start)
    stamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    if account.refresh_token_enc:  # cuenta conectada con su propio inicio de sesión
        url, token = f"{GRAPH}/me/events", _access_token(account)
    else:  # buzón dado de alta con permisos de aplicación
        url, token = f"{GRAPH}/users/{urllib.parse.quote(account.email)}/events", _app_token()
        MicrosoftAccount.objects.filter(pk=account.pk).update(last_used_at=timezone.now())
    event = _request(url, token=token, json_body={
        "subject": subject, "body": {"contentType": "HTML", "content": escape(message).replace("\n", "<br>")},
        "start": {"dateTime": stamp(local), "timeZone": settings.TIME_ZONE},
        "end": {"dateTime": stamp(local + timedelta(minutes=minutes)), "timeZone": settings.TIME_ZONE},
        "attendees": [{"emailAddress": {"address": address}, "type": "required"} for address in attendees],
        "isOnlineMeeting": True, "onlineMeetingProvider": "teamsForBusiness",
    })
    join_url = (event.get("onlineMeeting") or {}).get("joinUrl") or ""
    if not join_url:
        raise MicrosoftError("El evento se ha creado, pero Microsoft no ha devuelto enlace de Teams. Comprueba que la cuenta tiene licencia de Teams.")
    return {"id": event.get("id", ""), "join_url": join_url, "web_link": event.get("webLink", "")}
