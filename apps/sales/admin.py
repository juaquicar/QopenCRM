from django.contrib import admin

from .models import Subscription, SubscriptionEvent, HourBank, HourEntry, QuoteTemplate, Quote

for model in (Subscription, SubscriptionEvent, HourBank, HourEntry, QuoteTemplate, Quote):
    admin.site.register(model)
