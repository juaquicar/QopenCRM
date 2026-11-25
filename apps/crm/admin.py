from django.contrib import admin

from .models import Company, Contact, Stage, LossReason, Opportunity, OpportunityLine, Activity, Task

for model in (Company, Contact, Stage, LossReason, Opportunity, OpportunityLine, Activity, Task):
    admin.site.register(model)
