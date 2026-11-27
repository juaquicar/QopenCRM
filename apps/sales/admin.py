from django.contrib import admin

from .models import Subscription, SubscriptionEvent, HourBank, HourEntry, QuoteTemplate, Quote, Nda

for model in (Subscription, SubscriptionEvent, HourBank, HourEntry, QuoteTemplate, Quote, Nda):
    admin.site.register(model)
