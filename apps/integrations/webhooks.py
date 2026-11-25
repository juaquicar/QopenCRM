"""Webhooks salientes firmados (HMAC-SHA256 en la cabecera X-QopenCRM-Signature)."""
import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request

from django.db import transaction
from django.utils import timezone

from .models import Webhook, WebhookDelivery


def emit(event, payload):
    """Encola el evento para cada webhook suscrito y lo envía al confirmar la transacción."""
    ids = []
    for hook in Webhook.objects.filter(is_active=True):
        if hook.events and event not in hook.events:
            continue
        ids.append(WebhookDelivery.objects.create(webhook=hook, event=event, payload=payload).pk)
    if ids:
        transaction.on_commit(lambda: threading.Thread(target=_send_all, args=(ids,), daemon=True).start())


def _send_all(ids):
    for delivery in WebhookDelivery.objects.filter(pk__in=ids).select_related("webhook"):
        deliver(delivery)


def deliver(delivery):
    body = json.dumps({"event": delivery.event, "data": delivery.payload, "sent_at": timezone.now().isoformat()}).encode()
    signature = hmac.new(delivery.webhook.secret.encode(), body, hashlib.sha256).hexdigest()
    request = urllib.request.Request(
        delivery.webhook.url, data=body, method="POST",
        headers={"Content-Type": "application/json", "X-QopenCRM-Event": delivery.event, "X-QopenCRM-Signature": f"sha256={signature}"},
    )
    delivery.attempts += 1
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            delivery.status_code, delivery.error = response.status, ""
            delivery.delivered_at = timezone.now()
    except urllib.error.HTTPError as exc:
        delivery.status_code, delivery.error = exc.code, str(exc.reason)[:300]
    except Exception as exc:  # red caída, DNS, timeout: se reintenta con el comando retry_webhooks
        delivery.error = str(exc)[:300]
    delivery.save()
    return delivery.delivered_at is not None
