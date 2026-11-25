"""Exportación completa: un CSV por tabla dentro de un ZIP. Sin ataduras al proveedor."""
import csv
import io
import zipfile

from apps.accounts.permissions import scope
from apps.catalog.models import Product, ProductFamily
from apps.sales.models import HourBank, HourEntry, Quote, Subscription, SubscriptionEvent

from .models import Activity, Company, Contact, LossReason, Opportunity, OpportunityLine, Stage, Task

MODELS = [Company, Contact, Stage, LossReason, Opportunity, OpportunityLine, Activity, Task,
          ProductFamily, Product, Subscription, SubscriptionEvent, HourBank, HourEntry, Quote]


def model_csv(model, user):
    queryset = scope(model.objects.all(), user)
    if model is OpportunityLine:
        queryset = queryset.filter(opportunity__in=scope(Opportunity.objects.all(), user))
    if model is SubscriptionEvent:
        queryset = queryset.filter(subscription__in=scope(Subscription.objects.all(), user))
    fields = [f for f in model._meta.concrete_fields]
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([f.attname for f in fields])
    for obj in queryset.iterator():
        writer.writerow(["" if (v := getattr(obj, f.attname)) is None else v for f in fields])
    return out.getvalue()


def export_zip(user):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for model in MODELS:
            archive.writestr(f"{model._meta.model_name}.csv", "﻿" + model_csv(model, user))
        m2m = io.StringIO()
        writer = csv.writer(m2m)
        writer.writerow(["opportunity_id", "user_id"])
        writer.writerows(Opportunity.assignees.through.objects.filter(opportunity__in=scope(Opportunity.objects.all(), user)).values_list("opportunity_id", "user_id"))
        archive.writestr("opportunity_assignees.csv", m2m.getvalue())
    return buffer.getvalue()
