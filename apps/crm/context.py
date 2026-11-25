from pathlib import Path

from django.conf import settings
from django.utils import timezone

from apps.accounts.permissions import scope

from .models import Opportunity, Task

APK_VERSION = Path(settings.BASE_DIR) / "android" / "dist" / "VERSION"


def shell(request):
    """Contadores del menú: lo que el usuario tiene pendiente."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or request.headers.get("HX-Request"):
        return {}
    open_opps = scope(Opportunity.objects.filter(stage__is_won=False, stage__is_lost=False), user)
    return {
        "nav_overdue": Task.objects.filter(assignee=user, done_at__isnull=True, due_at__lt=timezone.now()).count(),
        "nav_no_action": open_opps.filter(next_action_at__isnull=True).count(),
        "nav_apk": APK_VERSION.read_text().strip() if APK_VERSION.exists() else "",
    }
