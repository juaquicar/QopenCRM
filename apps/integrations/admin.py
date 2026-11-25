from django.contrib import admin

from .models import Webhook, WebhookDelivery, MailboxState

for model in (Webhook, WebhookDelivery, MailboxState):
    admin.site.register(model)
