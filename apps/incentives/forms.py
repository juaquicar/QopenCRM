from django import forms

from apps.accounts.models import User
from apps.crm.forms import BaseForm

from .models import Incentive, ProductivityScore, ProductivityYear

COMMON = ["user", "concept", "company", "opportunity", "status"]
TAIL = ["paid", "paid_on", "notes"]


class _IncentiveForm(BaseForm):
    program = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(is_active=True)
        if "opportunity" in self.fields:
            self.fields["opportunity"].required = False

    def save(self, commit=True):
        self.instance.program = self.program
        return super().save(commit)


class SalesIncentiveForm(_IncentiveForm):
    program = Incentive.Program.SALES_NEW

    class Meta:
        model = Incentive
        fields = [*COMMON, "revenue", "direct_costs", "collected", *TAIL]


class QuotaIncentiveForm(_IncentiveForm):
    program = Incentive.Program.SALES_QUOTA

    class Meta:
        model = Incentive
        fields = [*COMMON, "quota_1", "quota_2", "quota_3", "quota_4", "quotas_collected", *TAIL]

    def clean_quotas_collected(self):
        value = self.cleaned_data["quotas_collected"]
        if value > 4:
            raise forms.ValidationError("Solo cuentan las cuatro primeras cuotas.")
        return value


class PmIncentiveForm(_IncentiveForm):
    program = Incentive.Program.PM

    class Meta:
        model = Incentive
        fields = [*COMMON, "revenue", "direct_costs", "planned_margin", "satisfaction_score", "kpi_deadlines", "kpi_costs", "collected", *TAIL]

    def clean_satisfaction_score(self):
        value = self.cleaned_data.get("satisfaction_score")
        if value is not None and not 0 <= value <= 10:
            raise forms.ValidationError("La nota va de 0 a 10.")
        return value


class ProductivityYearForm(BaseForm):
    class Meta:
        model = ProductivityYear
        fields = ["year", "profit", "previous_profit", "fund_pct", "growth_threshold", "peer_weight", "decision", "fund_override", "published", "notes"]


class ProductivityScoreForm(BaseForm):
    class Meta:
        model = ProductivityScore
        fields = ["year", "user", "peer_score", "management_score", "months", "eligible"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(is_active=True)

    def clean(self):
        data = super().clean()
        for name in ("peer_score", "management_score"):
            if data.get(name) is not None and not 0 <= data[name] <= 10:
                self.add_error(name, "La nota va de 0 a 10.")
        if data.get("months") is not None and not 1 <= data["months"] <= 12:
            self.add_error("months", "Entre 1 y 12 meses.")
        return data
