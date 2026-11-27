"""Permisos: rol + propietario por registro. Lo usan la web, la API y el MCP."""
from django.db.models import Q
from rest_framework.permissions import SAFE_METHODS, BasePermission


def scope(queryset, user):
    """Limita un queryset a lo que el usuario puede ver según su rol."""
    if user.sees_all:
        return queryset
    from apps.crm.models import Company, Opportunity

    name = queryset.model._meta.model_name
    own_opps = Opportunity.objects.filter(Q(owner=user) | Q(assignees=user)).values("pk")
    own_companies = Company.objects.filter(
        Q(owner=user) | Q(owner__isnull=True) | Q(opportunities__in=own_opps) | Q(tasks__assignee=user) | Q(tasks__assignees=user)
    ).values("pk")
    if name == "company":
        return queryset.filter(pk__in=own_companies)
    if name == "opportunity":
        return queryset.filter(pk__in=own_opps)
    if name == "task":
        from apps.crm.models import Task
        return queryset.filter(pk__in=Task.objects.filter(Task.for_user(user) | Q(created_by=user) | Q(opportunity__in=own_opps)).values("pk"))
    if name == "contact":  # también las personas que trabajan con una de sus empresas aunque no sea la principal
        from apps.crm.models import Contact
        return queryset.filter(pk__in=Contact.objects.filter(Q(company__in=own_companies) | Q(other_companies__in=own_companies)).values("pk"))
    if name in ("activity", "subscription", "hourbank"):
        return queryset.filter(company__in=own_companies)
    if name in ("quote", "nda"):
        return queryset.filter(opportunity__in=own_opps)
    if name == "hourentry":
        return queryset.filter(bank__company__in=own_companies)
    return queryset


class CrmPermission(BasePermission):
    message = "No tienes permiso para esta operación."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        if getattr(request, "auth", None) is not None and not user.can_use_api:
            return False
        if getattr(view, "superuser_only", False):
            return user.is_superuser
        if getattr(view, "catalog", False) and request.method not in SAFE_METHODS:
            return user.can_manage_catalog
        if getattr(view, "pipeline_config", False) and request.method not in SAFE_METHODS:
            return user.can_manage_pipeline
        return True

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return True
        if request.method == "DELETE":
            return request.user.can_delete and request.user.can_edit(obj)
        if getattr(view, "catalog", False) or getattr(view, "pipeline_config", False):
            return True
        return request.user.can_edit(obj)
