"""API REST v1. Mismos permisos que la web: rol + propietario por registro."""
from decimal import InvalidOperation

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import AccessLog, User, log_access
from apps.accounts.permissions import scope
from apps.catalog.models import Product, ProductFamily
from apps.crm import reports
from apps.crm.exporters import export_zip
from apps.crm.filters import CompanyFilter, ContactFilter, OpportunityFilter, TaskFilter
from apps.crm.models import Activity, Company, Contact, LossReason, Opportunity, OpportunityLine, Stage, Task
from apps.sales import services
from apps.sales.models import HourBank, HourEntry, Quote, QuoteTemplate, Subscription, SubscriptionEvent

from .webhooks import emit


class OpenView:
    """Las vistas de API se autentican solas (clave o sesión), sin pasar por el login web."""

    @classmethod
    def as_view(cls, *args, **kwargs):
        view = super().as_view(*args, **kwargs)
        view.login_required = False
        return view


def serializer_for(model_class, read_only=(), extra=None, exclude=None):
    meta_exclude = exclude

    class Meta:
        model = model_class
        if meta_exclude:
            exclude = meta_exclude
        else:
            fields = "__all__"
        read_only_fields = read_only

    return type(f"{model_class.__name__}Serializer", (serializers.ModelSerializer,), {"Meta": Meta, **(extra or {})})


class LineSerializer(serializers.ModelSerializer):
    total = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    product_code = serializers.CharField(source="product.code", read_only=True, default="")

    class Meta:
        model = OpportunityLine
        fields = ["id", "opportunity", "product", "product_code", "description", "billing_model", "period", "quantity", "unit", "unit_price", "discount", "total", "order"]
        extra_kwargs = {"billing_model": {"required": False}}


class OpportunitySerializer(serializers.ModelSerializer):
    lines = LineSerializer(many=True, read_only=True)
    company_name = serializers.CharField(source="company.name", read_only=True)
    stage_name = serializers.CharField(source="stage.name", read_only=True)
    first_year_value = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Opportunity
        fields = "__all__"
        read_only_fields = ["amount_mrr", "amount_services", "amount_one_off", "next_action_at", "closed_at", "position"]


class UserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source="role_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "name", "email", "role", "is_superuser"]


class ScopedViewSet(OpenView, viewsets.ModelViewSet):
    log = False

    def get_queryset(self):
        return scope(self.queryset.all(), self.request.user)

    def _check_parents(self, data):
        """No se puede colgar un registro de una cuenta u oportunidad que el usuario no ve."""
        user = self.request.user
        for field, model in (("company", Company), ("opportunity", Opportunity), ("contact", Contact)):
            obj = data.get(field)
            if obj is not None and not scope(model.objects.filter(pk=obj.pk), user).exists():
                raise PermissionDenied(f"No tienes acceso a ese registro ({field}).")

    def perform_create(self, serializer):
        self._check_parents(serializer.validated_data)
        extra = {}
        fields = {f.name for f in serializer.Meta.model._meta.fields}
        if "owner" in fields and not serializer.validated_data.get("owner"):
            extra["owner"] = self.request.user
        if "user" in fields:
            extra["user"] = self.request.user
        if "created_by" in fields:
            extra["created_by"] = self.request.user
        obj = serializer.save(**extra)
        log_access(self.request, AccessLog.Action.CREATE, obj)
        self.after_save(obj, created=True)

    def perform_update(self, serializer):
        self._check_parents(serializer.validated_data)
        obj = serializer.save()
        log_access(self.request, AccessLog.Action.UPDATE, obj)
        self.after_save(obj, created=False)

    def perform_destroy(self, instance):
        log_access(self.request, AccessLog.Action.DELETE, instance)
        instance.delete()

    def after_save(self, obj, created):
        pass

    def retrieve(self, request, *args, **kwargs):
        if self.log:
            log_access(request, AccessLog.Action.VIEW, self.get_object())
        return super().retrieve(request, *args, **kwargs)


class CompanyViewSet(ScopedViewSet):
    queryset = Company.objects.all()
    serializer_class = serializer_for(Company)
    filterset_class = CompanyFilter
    ordering_fields = ["name", "updated_at", "created_at"]
    log = True

    def after_save(self, obj, created):
        emit(f"company.{'created' if created else 'updated'}", {"id": obj.pk, "name": obj.name})

    @action(detail=True)
    def timeline(self, request, pk=None):
        items = self.get_object().activities.all()[:200]
        return Response(ActivityViewSet.serializer_class(items, many=True).data)

    @action(detail=True)
    def duplicates(self, request, pk=None):
        return Response(self.get_serializer(self.get_object().duplicates(), many=True).data)


class ContactViewSet(ScopedViewSet):
    queryset = Contact.objects.all()
    serializer_class = serializer_for(Contact, read_only=["erased_at"])
    filterset_class = ContactFilter
    log = True

    def after_save(self, obj, created):
        if created:
            emit("contact.created", {"id": obj.pk, "name": obj.full_name, "company": obj.company_id})

    @action(detail=True, methods=["post"])
    def erase(self, request, pk=None):
        """RGPD: derecho de supresión."""
        contact = self.get_object()
        if not (request.user.can_delete and request.user.can_edit(contact)):
            raise PermissionDenied()
        log_access(request, AccessLog.Action.ERASE, contact)
        contact.erase()
        return Response(status=status.HTTP_204_NO_CONTENT)


class OpportunityViewSet(ScopedViewSet):
    queryset = Opportunity.objects.select_related("company", "stage").prefetch_related("lines__product")
    serializer_class = OpportunitySerializer
    filterset_class = OpportunityFilter
    ordering_fields = ["expected_close", "updated_at", "created_at", "next_action_at", "priority", "amount_mrr"]
    log = True

    def perform_update(self, serializer):
        if "stage" in serializer.validated_data and serializer.validated_data["stage"] != serializer.instance.stage:
            raise ValidationError({"stage": "Para cambiar de etapa usa POST /opportunities/{id}/move/."})
        super().perform_update(serializer)

    def after_save(self, obj, created):
        if created:
            emit("opportunity.created", {"id": obj.pk, "name": obj.title, "company": obj.company_id})

    @action(detail=True, methods=["post"])
    def move(self, request, pk=None):
        opp = self.get_object()
        stage = get_object_or_404(Stage, pk=request.data.get("stage"))
        before, reason = opp.stage, None
        if stage.is_lost:
            reason = LossReason.objects.filter(pk=request.data.get("loss_reason") or 0).first()
            if not reason:
                raise ValidationError({"loss_reason": "Obligatorio al perder una oportunidad."})
        won_now = opp.move_to(stage, request.user, reason, str(request.data.get("loss_notes", ""))[:300])
        if won_now:
            services.fulfil_won(opp, request.user)
            emit("opportunity.won", {"id": opp.pk, "company": opp.company_id, "mrr": str(opp.amount_mrr)})
        if before != stage:
            emit("opportunity.stage_changed", {"id": opp.pk, "from": before.name, "to": stage.name})
            if stage.is_lost:
                emit("opportunity.lost", {"id": opp.pk, "reason": str(reason)})
        return Response(self.get_serializer(opp).data)

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        opp = self.get_object()
        if not opp.lines.exists():
            raise ValidationError("La oportunidad no tiene líneas.")
        template = QuoteTemplate.objects.filter(pk=request.data.get("template") or 0).first()
        quote = services.create_quote(opp, request.user, template)
        return Response(QuoteViewSet.serializer_class(quote, context={"request": request}).data, status=status.HTTP_201_CREATED)


class LineViewSet(ScopedViewSet):
    queryset = OpportunityLine.objects.select_related("product")
    serializer_class = LineSerializer
    filterset_fields = ["opportunity", "product", "billing_model"]

    def get_queryset(self):
        return self.queryset.filter(opportunity__in=scope(Opportunity.objects.all(), self.request.user))

    def perform_create(self, serializer):
        self._check_parents(serializer.validated_data)
        if not self.request.user.can_edit(serializer.validated_data["opportunity"]):
            raise PermissionDenied()
        product = serializer.validated_data.get("product")
        if not product and not serializer.validated_data.get("billing_model"):
            raise ValidationError({"billing_model": "Obligatorio si la línea no lleva producto."})
        extra = {"billing_model": product.billing_model, "unit": product.unit} if product else {}
        if product and "unit_price" not in serializer.validated_data:
            extra["unit_price"] = product.price
        serializer.save(**extra)


class ActivityViewSet(ScopedViewSet):
    queryset = Activity.objects.all()
    serializer_class = serializer_for(Activity, read_only=["user"])
    filterset_fields = ["company", "opportunity", "contact", "kind", "user"]
    search_fields = ["subject", "body"]


class TaskViewSet(ScopedViewSet):
    queryset = Task.objects.all()
    serializer_class = serializer_for(Task, read_only=["created_by", "reminded_at"], extra={
        "assignee": serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True), required=False),
    })
    filterset_class = TaskFilter
    ordering_fields = ["due_at", "created_at"]

    def perform_create(self, serializer):
        if not serializer.validated_data.get("assignee"):
            serializer.validated_data["assignee"] = self.request.user
        super().perform_create(serializer)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = self.get_object()
        if not task.done_at:
            task.complete(request.user)
        return Response(self.get_serializer(task).data)


class ProductViewSet(OpenView, viewsets.ModelViewSet):
    queryset = Product.objects.select_related("family")
    serializer_class = serializer_for(Product)
    filterset_fields = ["family", "billing_model", "status", "is_addon", "code", "external_id"]
    search_fields = ["name", "code", "description"]
    catalog = True


class FamilyViewSet(OpenView, viewsets.ModelViewSet):
    queryset = ProductFamily.objects.all()
    serializer_class = serializer_for(ProductFamily)
    catalog = True


class StageViewSet(OpenView, viewsets.ModelViewSet):
    queryset = Stage.objects.all()
    serializer_class = serializer_for(Stage)
    pipeline_config = True
    pagination_class = None


class LossReasonViewSet(OpenView, viewsets.ModelViewSet):
    queryset = LossReason.objects.all()
    serializer_class = serializer_for(LossReason)
    pipeline_config = True
    pagination_class = None


class SubscriptionViewSet(OpenView, viewsets.ReadOnlyModelViewSet):
    queryset = Subscription.objects.select_related("product", "company")
    serializer_class = serializer_for(Subscription, extra={"mrr": serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)})
    filterset_fields = ["company", "product", "status"]

    def get_queryset(self):
        return scope(self.queryset.all(), self.request.user)

    @action(detail=True)
    def events(self, request, pk=None):
        return Response(serializer_for(SubscriptionEvent)(self.get_object().events.all(), many=True).data)

    @action(detail=True, methods=["post"])
    def change(self, request, pk=None):
        """Cambio de plan, cantidad o precio; queda guardado como evento."""
        sub = self.get_object()
        if not request.user.can_edit(sub.company):
            raise PermissionDenied()
        product = Product.objects.filter(pk=request.data.get("product") or 0, billing_model="monthly").first()
        try:
            event = sub.change(request.user, product, request.data.get("quantity"), request.data.get("unit_price"), str(request.data.get("note", ""))[:300])
        except (InvalidOperation, ValueError):
            raise ValidationError("quantity y unit_price deben ser números.")
        emit("subscription.changed", {"id": sub.pk, "kind": event.kind, "company": sub.company_id, "mrr_delta": str(event.mrr_delta)})
        return Response(self.get_serializer(sub).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        sub = self.get_object()
        if not request.user.can_edit(sub.company):
            raise PermissionDenied()
        sub.cancel(request.user, str(request.data.get("note", ""))[:300])
        emit("subscription.changed", {"id": sub.pk, "kind": "cancel", "company": sub.company_id})
        return Response(self.get_serializer(sub).data)


class HourBankViewSet(ScopedViewSet):
    queryset = HourBank.objects.all()
    serializer_class = serializer_for(HourBank, extra={
        "hours_used": serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True),
        "hours_left": serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True),
    })
    filterset_fields = ["company", "product", "closed"]


class HourEntryViewSet(ScopedViewSet):
    queryset = HourEntry.objects.all()
    serializer_class = serializer_for(HourEntry, read_only=["user"])
    filterset_fields = ["bank"]

    def perform_create(self, serializer):
        bank = serializer.validated_data["bank"]
        if not scope(HourBank.objects.filter(pk=bank.pk), self.request.user).exists():
            raise PermissionDenied()
        serializer.save(user=self.request.user)


class QuoteViewSet(OpenView, viewsets.ReadOnlyModelViewSet):
    queryset = Quote.objects.all()
    serializer_class = serializer_for(Quote, exclude=["pdf"])
    filterset_fields = ["opportunity", "status", "number"]

    def get_queryset(self):
        return scope(self.queryset.all(), self.request.user)

    @action(detail=True)
    def pdf(self, request, pk=None):
        quote = self.get_object()
        log_access(request, AccessLog.Action.VIEW, quote)
        return HttpResponse(quote.pdf.read(), content_type="application/pdf")


class UserViewSet(OpenView, viewsets.ReadOnlyModelViewSet):
    queryset = User.objects.filter(is_active=True).select_related("role")
    serializer_class = UserSerializer
    pagination_class = None


class MeView(OpenView, APIView):
    def get(self, request):
        user = request.user
        data = UserSerializer(user).data
        data["permissions"] = {k: getattr(user, k) for k in (
            "sees_all", "edits_all", "can_delete", "can_export", "can_manage_catalog", "can_manage_pipeline", "can_see_reports")}
        return Response(data)


class ReportView(OpenView, APIView):
    """Informes: valor del pipeline por etapa, conversión, ciclo medio y previsión mensual."""

    superuser_only = True

    def get(self, request):
        return Response(reports.summary())


class ExportView(OpenView, APIView):
    def get(self, request):
        if not request.user.can_export:
            raise PermissionDenied()
        log_access(request, AccessLog.Action.EXPORT, label="export_all")
        response = HttpResponse(export_zip(request.user), content_type="application/zip")
        response["Content-Disposition"] = f'attachment; filename="qopencrm-{timezone.localdate():%Y%m%d}.zip"'
        return response
