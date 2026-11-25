from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.integrations.models import WebhookDelivery
from apps.integrations.webhooks import deliver


class Command(BaseCommand):
    help = "Reintenta las entregas de webhook fallidas (máximo 6 intentos, últimas 48 h)."

    def handle(self, *args, **options):
        pending = WebhookDelivery.objects.filter(
            delivered_at__isnull=True, attempts__lt=6, created_at__gte=timezone.now() - timedelta(hours=48), webhook__is_active=True
        ).select_related("webhook")
        ok = sum(1 for d in pending if deliver(d))
        self.stdout.write(f"{ok} entregas recuperadas")
