"""Filtros de oportunidades y tareas, compartidos por la web y la API."""
from datetime import timedelta

import django_filters as df
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Product, ProductFamily

from .models import Company, Event, Contact, Opportunity, Stage, Task


def _day_bounds():
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


class OpportunityFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Buscar")
    status = df.ChoiceFilter(method="by_status", label="Estado", empty_label=None,
                             choices=[("open", "Abiertas"), ("won", "Ganadas"), ("lost", "Perdidas"), ("all", "Todas")])
    hold = df.ChoiceFilter(method="by_hold", label="Paradas por el cliente", empty_label=None,
                           choices=[("", "Con paradas"), ("hide", "Sin paradas"), ("only", "Solo paradas")])
    stage = df.ModelChoiceFilter(queryset=Stage.objects.all(), label="Etapa", empty_label="Todas las etapas")
    owner = df.ModelChoiceFilter(queryset=User.objects.filter(is_active=True), label="Responsable", empty_label="Cualquier responsable")
    assignee = df.ModelChoiceFilter(queryset=User.objects.filter(is_active=True), method="by_assignee", label="Asignada a", empty_label="Todo el equipo")
    product = df.ModelChoiceFilter(queryset=Product.objects.all(), field_name="lines__product", label="Producto", empty_label="Todos los productos")
    family = df.ModelChoiceFilter(queryset=ProductFamily.objects.all(), field_name="lines__product__family", label="Familia", empty_label="Todas las familias")
    priority = df.ChoiceFilter(choices=Opportunity.Priority.choices, label="Prioridad", empty_label="Cualquier prioridad")
    action = df.ChoiceFilter(method="by_action", label="Próxima acción", empty_label="Cualquier próxima acción",
                             choices=[("none", "Sin próxima acción"), ("overdue", "Vencida"), ("today", "Hoy"), ("week", "Próximos 7 días")])
    close_from = df.DateFilter(field_name="expected_close", lookup_expr="gte", label="Cierre desde")
    close_to = df.DateFilter(field_name="expected_close", lookup_expr="lte", label="Cierre hasta")
    created_from = df.DateFilter(field_name="created_at", lookup_expr="date__gte", label="Creada desde")
    created_to = df.DateFilter(field_name="created_at", lookup_expr="date__lte", label="Creada hasta")
    company = df.ModelChoiceFilter(queryset=Company.objects.all(), label="Cuenta")

    class Meta:
        model = Opportunity
        fields = []

    @property
    def qs(self):
        return super().qs.distinct()

    def search(self, queryset, name, value):
        return queryset.filter(Q(title__icontains=value) | Q(company__name__icontains=value) | Q(description__icontains=value))

    def by_status(self, queryset, name, value):
        if value == "won":
            return queryset.filter(stage__is_won=True)
        if value == "lost":
            return queryset.filter(stage__is_lost=True)
        if value == "open":
            return queryset.filter(stage__is_won=False, stage__is_lost=False)
        return queryset

    def by_hold(self, queryset, name, value):
        """Paradas por el cliente: mezcladas con el resto (por defecto), ocultas o solas."""
        held = Q(on_hold_until__isnull=False, stage__is_won=False, stage__is_lost=False)
        if value == "only":
            return queryset.filter(held)
        if value == "hide":
            return queryset.exclude(held)
        return queryset

    def by_assignee(self, queryset, name, value):
        return queryset.filter(Q(owner=value) | Q(assignees=value))

    def by_action(self, queryset, name, value):
        start, end = _day_bounds()
        now = timezone.now()
        if value == "none":
            return queryset.filter(next_action_at__isnull=True)
        if value == "overdue":
            return queryset.filter(next_action_at__lt=now)
        if value == "today":
            return queryset.filter(next_action_at__gte=start, next_action_at__lt=end)
        return queryset.filter(next_action_at__gte=start, next_action_at__lt=start + timedelta(days=7))


class TaskFilter(df.FilterSet):
    when = df.ChoiceFilter(method="by_when", label="Cuándo",
                           choices=[("overdue", "Vencidas"), ("today", "Hoy"), ("week", "Próximos 7 días"), ("done", "Hechas")])
    assignee = df.ModelChoiceFilter(queryset=User.objects.filter(is_active=True), label="Responsable")
    kind = df.ChoiceFilter(choices=Task.Kind.choices, label="Tipo")
    opportunity = df.ModelChoiceFilter(queryset=Opportunity.objects.all())
    company = df.ModelChoiceFilter(queryset=Company.objects.all())

    class Meta:
        model = Task
        fields = []

    def by_when(self, queryset, name, value):
        start, end = _day_bounds()
        if value == "done":
            return queryset.filter(done_at__isnull=False)
        queryset = queryset.filter(done_at__isnull=True)
        if value == "overdue":
            return queryset.filter(due_at__lt=timezone.now())
        if value == "today":
            return queryset.filter(due_at__gte=start, due_at__lt=end)
        return queryset.filter(due_at__gte=start, due_at__lt=start + timedelta(days=7))


class CompanyFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Buscar")
    kind = df.ChoiceFilter(choices=Company.Kind.choices, label="Tipo", empty_label="Todos los tipos")
    owner = df.ModelChoiceFilter(queryset=User.objects.filter(is_active=True), label="Responsable", empty_label="Cualquier responsable")
    event = df.ModelChoiceFilter(queryset=Event.objects.all(), label="Feria o evento", empty_label="Cualquier feria o evento")

    class Meta:
        model = Company
        fields = ["sector", "province"]

    def search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) | Q(tax_id__icontains=value) | Q(domain__icontains=value) | Q(city__icontains=value)
            | Q(contacts__first_name__icontains=value) | Q(contacts__last_name__icontains=value) | Q(contacts__email__icontains=value)
        ).distinct()


class ContactFilter(df.FilterSet):
    q = df.CharFilter(method="search", label="Buscar")
    company = df.ModelChoiceFilter(queryset=Company.objects.all())

    legal_basis = df.ChoiceFilter(choices=Contact.LegalBasis.choices, label="Base legal", empty_label="Cualquier base legal")

    class Meta:
        model = Contact
        fields = ["consent_marketing"]

    def search(self, queryset, name, value):
        return queryset.filter(
            Q(first_name__icontains=value) | Q(last_name__icontains=value) | Q(email__icontains=value)
            | Q(company__name__icontains=value) | Q(position__icontains=value)
        )
