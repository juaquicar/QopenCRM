from pathlib import Path

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.permissions import scope

from .models import Notification, Opportunity, Task

APK_VERSION = Path(settings.BASE_DIR) / "android" / "dist" / "VERSION"


# En el móvil: pestaña de la barra inferior a la que pertenece cada pantalla, y pantallas de detalle con su «atrás»
TABS = {"home": "home", "pipeline": "leads", "opportunity": "leads", "companies": "companies", "company": "companies",
        "contacts": "contacts", "contact": "contacts", "search": "", "share": ""}
PARENTS = {"opportunity": "pipeline", "company": "companies", "contact": "contacts", "event": "events", "quote": "quotes",
           "quote_new": "quotes", "quote_edit": "quotes", "article": "articles", "article_new": "articles", "article_edit": "articles",
           "import": "settings", "manual_role": "manual", "search": "home", "share": "home"}
CREATE_FROM = {"home", "pipeline", "companies", "contacts", "tasks", "search"}  # pantallas con el botón flotante de alta


def shell(request):
    """Contadores del menú: lo que el usuario tiene pendiente."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or request.headers.get("HX-Request"):
        return {}
    name = request.resolver_match.url_name if request.resolver_match else ""
    open_opps = scope(Opportunity.objects.filter(stage__is_won=False, stage__is_lost=False), user)
    return {
        "nav_overdue": Task.of(user).filter(done_at__isnull=True, due_at__lt=timezone.now()).count(),
        "nav_no_action": open_opps.filter(next_action_at__isnull=True).count(),
        "nav_mentions": Notification.objects.filter(recipient=user, read_at__isnull=True).count(),
        "nav_tab": TABS.get(name, "more"),
        "nav_back": reverse(PARENTS[name]) if name in PARENTS else "",
        "nav_fab": name in CREATE_FROM,
        "nav_apk": APK_VERSION.read_text().strip() if APK_VERSION.exists() else "",
    }
