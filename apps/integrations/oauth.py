"""Servidor de autorización OAuth 2.1 del MCP de QopenCRM.

Los conectores web de IA (Claude, ChatGPT) solo dejan añadir un servidor MCP remoto por URL + OAuth: no admiten
cabeceras propias. Este módulo es el servidor de autorización que esos clientes descubren y usan. Sigue este
diseño, sin proveedor externo:

  1. GET  /.well-known/oauth-protected-resource    (RFC 9728) -> quién autoriza
  2. GET  /.well-known/oauth-authorization-server  (RFC 8414) -> dónde y con qué
  3. POST /mcp/oauth/register                      (RFC 7591) -> el cliente se da de alta solo
  4. GET/POST /mcp/oauth/authorize                 -> sesión del CRM + consentimiento, código con PKCE
  5. POST /mcp/oauth/token                         -> authorization_code | refresh_token

Decisiones de seguridad:
- PKCE S256 obligatorio: se rechaza `plain` y la ausencia de reto.
- Códigos de un solo uso, cinco minutos de vida, atados a cliente + redirect_uri + reto.
- redirect_uri con comparación exacta; solo https, o http en loopback para clientes locales.
- Secretos y tokens solo se guardan hasheados (SHA-256); el valor en claro se entrega una vez.
- El consentimiento exige sesión iniciada en el CRM (código por correo); el acceso hereda los permisos de esa persona.
- El refresh token rota en cada uso; reutilizar uno ya gastado revoca todos los accesos de esa persona a esa aplicación.
- El token queda atado al recurso (`resource`, RFC 8707). Límite de peticiones en registro y token.
"""
import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .models import OAuthClient, OAuthCode, OAuthToken, hash_key

SCOPE = "crm"
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
REGISTER_RATE = 10   # altas de cliente por IP y minuto
TOKEN_RATE = 60      # llamadas a /token por cliente y minuto
AUTH_METHODS = ("none", "client_secret_post", "client_secret_basic")


def origin():
    return settings.SITE_URL.rstrip("/")


def resource_url():
    return origin() + "/mcp/"


def metadata_url():
    return origin() + "/.well-known/oauth-protected-resource"


def _json(data, status=200):
    response = JsonResponse(data, status=status)
    response["Cache-Control"] = "no-store"
    response["Access-Control-Allow-Origin"] = "*"  # el descubrimiento y el canje los hacen también clientes en navegador
    response["Access-Control-Allow-Headers"] = "Content-Type, Authorization, MCP-Protocol-Version"
    return response


def _error(error, description="", status=400):
    return _json({"error": error, "error_description": description}, status=status)


def _ip(request):
    return (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR") or "unknown")


def _rate_ok(key, limit):
    cache_key = f"oauth:{key}:{int(time.time() // 60)}"
    count = cache.get(cache_key, 0)
    if count >= limit:
        return False
    cache.set(cache_key, count + 1, timeout=90)
    return True


def valid_redirect_uri(uri):
    """https obligatorio, salvo loopback para clientes que corren en el propio equipo."""
    if not isinstance(uri, str) or not uri or len(uri) > 500:
        return False
    try:
        parsed = urlparse(uri)
    except ValueError:
        return False
    if parsed.fragment:
        return False
    if parsed.scheme == "https" and parsed.netloc:
        return True
    return parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")


def _verify_pkce(verifier, challenge):
    if not verifier or not challenge or not 43 <= len(verifier) <= 128:
        return False
    try:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    return secrets.compare_digest(base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"), challenge)


def _resource_ok(resource):
    return not resource or resource.rstrip("/") == resource_url().rstrip("/")


def _redirect(uri, params):
    params = {**params, "iss": origin()}
    return HttpResponseRedirect(uri + ("&" if "?" in uri else "?") + urlencode(params), headers={"Cache-Control": "no-store"})


# HttpResponseRedirect solo admite http/https/ftp; los clientes nativos usan loopback http, que ya entra.


# ---------------------------------------------------------------- descubrimiento
@login_not_required
@require_http_methods(["GET", "OPTIONS"])
def protected_resource_metadata(request, path=""):
    return _json({
        "resource": resource_url(), "resource_name": "QopenCRM", "authorization_servers": [origin()],
        "scopes_supported": [SCOPE], "bearer_methods_supported": ["header"],
    })


@login_not_required
@require_http_methods(["GET", "OPTIONS"])
def authorization_server_metadata(request, path=""):
    base = origin()
    return _json({
        "issuer": base, "authorization_endpoint": f"{base}/mcp/oauth/authorize", "token_endpoint": f"{base}/mcp/oauth/token",
        "registration_endpoint": f"{base}/mcp/oauth/register", "scopes_supported": [SCOPE], "response_types_supported": ["code"],
        "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": list(AUTH_METHODS), "code_challenge_methods_supported": ["S256"],
        "authorization_response_iss_parameter_supported": True,
    })


# ------------------------------------------------------------ alta de clientes
@csrf_exempt
@login_not_required
@require_http_methods(["POST", "OPTIONS"])
def register(request):
    if request.method == "OPTIONS":
        return _json({})
    if not _rate_ok(f"register:{_ip(request)}", REGISTER_RATE):
        return _error("temporarily_unavailable", "Demasiadas altas seguidas.", status=429)
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return _error("invalid_client_metadata", "JSON no válido.")
    if not isinstance(body, dict):
        return _error("invalid_client_metadata", "El cuerpo debe ser un objeto JSON.")
    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris or len(redirect_uris) > 5:
        return _error("invalid_redirect_uri", "redirect_uris: entre 1 y 5.")
    for uri in redirect_uris:
        if not valid_redirect_uri(uri):
            return _error("invalid_redirect_uri", f"redirect_uri no válido: {str(uri)[:80]}")
    method = body.get("token_endpoint_auth_method") or "none"
    if method not in AUTH_METHODS:
        return _error("invalid_client_metadata", "token_endpoint_auth_method no soportado.")
    client = OAuthClient(client_id="mcp_" + secrets.token_urlsafe(18), client_name=str(body.get("client_name") or "")[:200],
                         redirect_uris=redirect_uris, token_endpoint_auth_method=method)
    raw_secret = None
    if method != "none":
        raw_secret = secrets.token_urlsafe(32)
        client.secret_hash = hash_key(raw_secret)
    client.save()
    answer = {
        "client_id": client.client_id, "client_id_issued_at": int(client.created_at.timestamp()), "redirect_uris": redirect_uris,
        "token_endpoint_auth_method": method, "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"], "scope": SCOPE,
    }
    if client.client_name:
        answer["client_name"] = client.client_name
    if raw_secret:
        answer.update(client_secret=raw_secret, client_secret_expires_at=0)
    return _json(answer, status=201)


# ------------------------------------------------------------------ autorizar
def _parse_authorize(params):
    """Devuelve (contexto, error_redirigible, error_fatal). Sin cliente o redirección válidos no se redirige a ningún sitio."""
    client = OAuthClient.objects.filter(client_id=params.get("client_id") or "").first()
    redirect_uri = params.get("redirect_uri") or ""
    if client is None:
        return None, None, "La aplicación no está registrada (client_id desconocido)."
    if redirect_uri not in client.redirect_uris:
        return None, None, "La dirección de retorno no coincide con la registrada por la aplicación."
    state = params.get("state") or ""
    if params.get("response_type") != "code":
        return None, (redirect_uri, "unsupported_response_type", "Solo response_type=code.", state), None
    challenge = params.get("code_challenge") or ""
    if not challenge or len(challenge) > 128:
        return None, (redirect_uri, "invalid_request", "PKCE obligatorio (code_challenge).", state), None
    if (params.get("code_challenge_method") or "plain") != "S256":
        return None, (redirect_uri, "invalid_request", "code_challenge_method debe ser S256.", state), None
    resource = params.get("resource") or ""
    if not _resource_ok(resource):
        return None, (redirect_uri, "invalid_target", "resource no corresponde a este servidor.", state), None
    return {"client": client, "redirect_uri": redirect_uri, "state": state, "code_challenge": challenge, "resource": resource}, None, None


@require_http_methods(["GET", "POST"])
def authorize(request):
    """Con la sesión del CRM iniciada, la persona ve qué aplicación pide acceso y decide."""
    from .mcp import tools_for

    source = request.POST if request.method == "POST" else request.GET
    ctx, redirectable, fatal = _parse_authorize(source)
    if fatal:
        return render(request, "integrations/oauth_error.html", {"error": fatal}, status=400)
    if redirectable:
        uri, error, description, state = redirectable
        return _redirect(uri, {"error": error, "error_description": description, **({"state": state} if state else {})})
    user = request.user
    allowed = user.can_use_api
    if request.method == "GET":
        tools = tools_for(user)
        return render(request, "integrations/oauth_consent.html", {
            **ctx, "client_name": ctx["client"].client_name or "Una aplicación", "redirect_host": urlparse(ctx["redirect_uri"]).hostname or "",
            "allowed": allowed, "n_tools": len(tools), "n_write": sum(1 for t in tools if t["write"]),
        })
    state = {"state": ctx["state"]} if ctx["state"] else {}
    if source.get("decision") != "allow":
        return _redirect(ctx["redirect_uri"], {"error": "access_denied", "error_description": "La persona ha denegado el acceso.", **state})
    if not allowed:
        return _redirect(ctx["redirect_uri"], {"error": "access_denied", "error_description": "El rol no permite el acceso por MCP.", **state})
    raw = secrets.token_urlsafe(32)
    OAuthCode.objects.create(code_hash=hash_key(raw), client=ctx["client"], user=user, redirect_uri=ctx["redirect_uri"],
                             code_challenge=ctx["code_challenge"], resource=ctx["resource"], expires_at=timezone.now() + CODE_TTL)
    return _redirect(ctx["redirect_uri"], {"code": raw, **state})


# ---------------------------------------------------------------------- token
def _client_from(request):
    client_id, secret = request.POST.get("client_id"), request.POST.get("client_secret")
    header = request.headers.get("Authorization", "")
    if header[:6].lower() == "basic ":
        try:
            client_id, _, secret = base64.b64decode(header[6:].strip()).decode("utf-8").partition(":")
        except ValueError:
            return None
    client = OAuthClient.objects.filter(client_id=client_id or "").first()
    if client is None:
        return None
    if client.token_endpoint_auth_method != "none" and not (secret and secrets.compare_digest(hash_key(secret), client.secret_hash)):
        return None
    return client


def _issue(client, user, resource):
    access, refresh = "scrmo_" + secrets.token_urlsafe(32), "scrmr_" + secrets.token_urlsafe(32)
    now = timezone.now()
    OAuthToken.objects.create(client=client, user=user, access_hash=hash_key(access), refresh_hash=hash_key(refresh), resource=resource,
                              access_expires_at=now + ACCESS_TTL, refresh_expires_at=now + REFRESH_TTL)
    return _json({"access_token": access, "token_type": "Bearer", "expires_in": int(ACCESS_TTL.total_seconds()),
                  "refresh_token": refresh, "scope": SCOPE})


@csrf_exempt
@login_not_required
@require_http_methods(["POST", "OPTIONS"])
def token(request):
    if request.method == "OPTIONS":
        return _json({})
    client = _client_from(request)
    if client is None:
        return _error("invalid_client", "Cliente desconocido o mal autenticado.", status=401)
    if not _rate_ok(f"token:{client.client_id}", TOKEN_RATE):
        return _error("temporarily_unavailable", "Demasiadas peticiones.", status=429)
    grant, now = request.POST.get("grant_type"), timezone.now()
    if grant == "authorization_code":
        code = OAuthCode.objects.select_related("user").filter(code_hash=hash_key(request.POST.get("code") or ""), client=client).first()
        if code is None:
            return _error("invalid_grant", "Código no válido.")
        if code.used_at:  # un código reutilizado es señal de robo: se anula lo que se emitió con él
            OAuthToken.objects.filter(client=client, user=code.user, revoked_at__isnull=True, created_at__gte=code.used_at).update(revoked_at=now)
            return _error("invalid_grant", "Código ya utilizado.")
        if code.expires_at < now or code.redirect_uri != (request.POST.get("redirect_uri") or ""):
            return _error("invalid_grant", "Código caducado o redirección distinta.")
        if not _verify_pkce(request.POST.get("code_verifier"), code.code_challenge):
            return _error("invalid_grant", "PKCE no coincide.")
        if not _resource_ok(request.POST.get("resource") or "") or not code.user.is_active or not code.user.can_use_api:
            return _error("invalid_grant", "Acceso no permitido.")
        if not OAuthCode.objects.filter(pk=code.pk, used_at__isnull=True).update(used_at=now):
            return _error("invalid_grant", "Código ya utilizado.")
        return _issue(client, code.user, code.resource)
    if grant == "refresh_token":
        current = OAuthToken.objects.select_related("user").filter(refresh_hash=hash_key(request.POST.get("refresh_token") or ""), client=client).first()
        if current is None:
            return _error("invalid_grant", "Refresh token no válido.")
        if current.revoked_at:  # refresh ya gastado: alguien más lo tiene; se corta todo el acceso de esa persona a esta aplicación
            OAuthToken.objects.filter(client=client, user=current.user, revoked_at__isnull=True).update(revoked_at=now)
            return _error("invalid_grant", "Refresh token ya utilizado.")
        if current.refresh_expires_at < now or not current.user.is_active or not current.user.can_use_api:
            return _error("invalid_grant", "Acceso caducado o no permitido.")
        if not OAuthToken.objects.filter(pk=current.pk, revoked_at__isnull=True).update(revoked_at=now):
            return _error("invalid_grant", "Refresh token ya utilizado.")
        return _issue(client, current.user, current.resource)
    return _error("unsupported_grant_type", "Solo authorization_code y refresh_token.")


def user_from_access_token(raw):
    """Persona a la que pertenece un access token vigente, o None."""
    found = OAuthToken.objects.select_related("user", "user__role").filter(access_hash=hash_key(raw), revoked_at__isnull=True).first()
    if found is None or found.access_expires_at < timezone.now() or not found.user.is_active:
        return None
    OAuthToken.objects.filter(pk=found.pk).update(last_used_at=timezone.now())
    return found.user


@require_POST
def revoke_app(request, client_pk):
    """Desde Ajustes: la persona corta el acceso de una aplicación a su cuenta."""
    OAuthToken.objects.filter(user=request.user, client_id=client_pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
    return HttpResponseRedirect("/ajustes/#mcp")


def connected_apps(user):
    """Aplicaciones con acceso vigente a la cuenta de esta persona."""
    now, apps = timezone.now(), {}
    for item in OAuthToken.objects.select_related("client").filter(user=user, revoked_at__isnull=True, refresh_expires_at__gt=now):
        entry = apps.setdefault(item.client_id, {"client": item.client, "since": item.created_at, "last_used_at": item.last_used_at})
        entry["since"] = min(entry["since"], item.created_at)
        if item.last_used_at and (not entry["last_used_at"] or item.last_used_at > entry["last_used_at"]):
            entry["last_used_at"] = item.last_used_at
    return list(apps.values())
