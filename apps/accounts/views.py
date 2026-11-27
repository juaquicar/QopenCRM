from django.conf import settings
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_not_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .mailer import send_internal
from .models import AccessLog, LoginCode, User, log_access


def _send_code(user):
    code = LoginCode.issue(user)
    if code is None:
        return
    send_internal(
        f"{code} es tu código de acceso",
        f"Hola {user.name or ''},\n\nTu código de acceso es: {code}\n\n"
        f"Caduca en {settings.LOGIN_CODE_TTL_MINUTES} minutos. Si no lo has pedido tú, ignora este correo.",
        user.email,
    )


@login_not_required
def login(request):
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "GET" and request.GET.get("next"):
        request.session["login_next"] = request.GET["next"]  # p. ej. la autorización de un asistente, que llega sin sesión
    if request.method == "POST":
        email = request.POST.get("email", "").strip().lower()
        if email:
            user = User.objects.filter(email=email, is_active=True).first()
            # misma respuesta exista o no el usuario: no se revelan cuentas. El límite de envíos lo aplica LoginCode.issue
            if user:
                _send_code(user)
            request.session["login_email"] = email
            return redirect("login_verify")
    return render(request, "accounts/login.html", {"email": request.session.get("login_email", "")})


@login_not_required
def login_verify(request):
    email = request.session.get("login_email")
    if not email:
        return redirect("login")
    error = ""
    if request.method == "POST":
        user = User.objects.filter(email=email, is_active=True).first()
        code = "".join(ch for ch in request.POST.get("code", "") if ch.isdigit())
        if user and len(code) == 6 and LoginCode.verify(user, code):
            target = request.GET.get("next", "") or request.session.get("login_next", "")
            if user.last_login is None and not user.task_emails:
                # los avisos por correo se dejan apagados hasta el primer acceso, para no escribir a quien aún no usa el CRM
                user.task_emails = True
                user.save(update_fields=["task_emails"])
            auth_login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            for key in ("login_email", "login_sent_at", "login_next"):
                request.session.pop(key, None)
            log_access(request, AccessLog.Action.LOGIN, label="session")
            safe = url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure())
            return redirect(target if target and safe else "home")
        error = "Código incorrecto o caducado. Revisa el correo o pide uno nuevo."
    return render(request, "accounts/verify.html", {"email": email, "error": error})


@require_POST
def logout(request):
    auth_logout(request)
    return redirect("login")


@login_not_required
def assetlinks(request):
    """Digital Asset Links: vincula el dominio con la app Android."""
    return JsonResponse([{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {"namespace": "android_app", "package_name": settings.ANDROID_PACKAGE, "sha256_cert_fingerprints": settings.ANDROID_SHA256},
    }] if settings.ANDROID_SHA256 else [], safe=False)
