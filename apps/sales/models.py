from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone

from apps.catalog.models import BillingModel, Product
from apps.crm.models import Company, Opportunity, OpportunityLine

User = settings.AUTH_USER_MODEL


class Subscription(models.Model):
    """Suscripción viva de un cliente. Nace al ganar la oportunidad."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Activa"
        PAUSED = "paused", "En pausa"
        CANCELLED = "cancelled", "Baja"

    company = models.ForeignKey(Company, verbose_name="empresa", on_delete=models.CASCADE, related_name="subscriptions")
    product = models.ForeignKey(Product, verbose_name="plan", null=True, blank=True, on_delete=models.PROTECT, related_name="subscriptions")
    label = models.CharField("concepto", max_length=200, blank=True, help_text="Para cuotas que no salen del catálogo.")
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.SET_NULL, related_name="subscriptions")
    quantity = models.DecimalField("cantidad", max_digits=10, decimal_places=2, default=1)
    unit_price = models.DecimalField("precio", max_digits=12, decimal_places=2, default=0)
    discount = models.DecimalField("dto. %", max_digits=5, decimal_places=2, default=0)
    period = models.CharField("periodo", max_length=8, choices=OpportunityLine.Period.choices, default=OpportunityLine.Period.MONTHLY)
    started_on = models.DateField("inicio", default=timezone.localdate)
    renews_on = models.DateField("renovación", null=True, blank=True)
    status = models.CharField("estado", max_length=10, choices=Status.choices, default=Status.ACTIVE)
    cancelled_on = models.DateField("fecha de baja", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["company__name", "product__name"]
        verbose_name = "suscripción"
        verbose_name_plural = "suscripciones"

    def __str__(self):
        return f"{self.company} · {self.plan_name}"

    @property
    def plan_name(self):
        return str(self.product) if self.product_id else (self.label or "Cuota recurrente")

    @property
    def mrr(self):
        if self.status != self.Status.ACTIVE:
            return Decimal(0)
        total = self.quantity * self.unit_price * (100 - self.discount) / 100
        if self.period == OpportunityLine.Period.ANNUAL:
            total /= 12
        return total.quantize(Decimal("0.01"))

    def record(self, kind, user=None, mrr_before=Decimal(0), from_product=None, note=""):
        return SubscriptionEvent.objects.create(
            subscription=self, kind=kind, user=user, from_product=from_product, to_product=self.product,
            quantity=self.quantity, mrr_delta=self.mrr - mrr_before, note=note,
        )

    def change(self, user=None, product=None, quantity=None, unit_price=None, note=""):
        """Cambio de plan o de cantidad, guardado como evento para medir expansión y contracción."""
        before, old_product = self.mrr, self.product
        if product is not None:
            self.product = product
        if quantity not in (None, ""):
            self.quantity = Decimal(str(quantity))
        if unit_price not in (None, ""):
            self.unit_price = Decimal(str(unit_price))
        self.save()
        delta = self.mrr - before
        kind = SubscriptionEvent.Kind.UPGRADE if delta >= 0 else SubscriptionEvent.Kind.DOWNGRADE
        return self.record(kind, user, before, old_product if old_product != self.product else None, note)

    def cancel(self, user=None, note=""):
        before = self.mrr
        self.status, self.cancelled_on = self.Status.CANCELLED, timezone.localdate()
        self.save()
        return self.record(SubscriptionEvent.Kind.CANCEL, user, before, note=note)


class SubscriptionEvent(models.Model):
    class Kind(models.TextChoices):
        NEW = "new", "Alta"
        UPGRADE = "upgrade", "Expansión"
        DOWNGRADE = "downgrade", "Contracción"
        CANCEL = "cancel", "Baja"
        REACTIVATE = "reactivate", "Reactivación"

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="events")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    from_product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    to_product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=1)
    mrr_delta = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    note = models.CharField(max_length=300, blank=True)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    occurred_on = models.DateField(default=timezone.localdate, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_on", "-id"]
        verbose_name = "evento de suscripción"
        verbose_name_plural = "eventos de suscripción"


class HourBank(models.Model):
    """Bolsa de horas vendida (auditoría, consultoría, desarrollo, formación)."""

    company = models.ForeignKey(Company, verbose_name="empresa", on_delete=models.CASCADE, related_name="hour_banks")
    product = models.ForeignKey(Product, verbose_name="servicio", null=True, blank=True, on_delete=models.SET_NULL, related_name="hour_banks")
    opportunity = models.ForeignKey(Opportunity, null=True, blank=True, on_delete=models.SET_NULL, related_name="hour_banks")
    name = models.CharField("concepto", max_length=200)
    hours_sold = models.DecimalField("horas vendidas", max_digits=8, decimal_places=2)
    hourly_rate = models.DecimalField("precio/hora", max_digits=10, decimal_places=2, null=True, blank=True)
    sold_on = models.DateField("fecha", default=timezone.localdate)
    closed = models.BooleanField("cerrada", default=False)

    class Meta:
        ordering = ["closed", "-sold_on"]
        verbose_name = "bolsa de horas"
        verbose_name_plural = "bolsas de horas"

    def __str__(self):
        return self.name

    @property
    def hours_used(self):
        return self.entries.aggregate(t=Sum("hours"))["t"] or Decimal(0)

    @property
    def hours_left(self):
        return self.hours_sold - self.hours_used

    @property
    def used_pct(self):
        return min(100, round(self.hours_used / self.hours_sold * 100)) if self.hours_sold else 0


class HourEntry(models.Model):
    bank = models.ForeignKey(HourBank, on_delete=models.CASCADE, related_name="entries")
    worked_on = models.DateField("fecha", default=timezone.localdate)
    hours = models.DecimalField("horas", max_digits=6, decimal_places=2)
    description = models.CharField("trabajo", max_length=300)
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ["-worked_on", "-id"]
        verbose_name = "consumo de horas"
        verbose_name_plural = "consumos de horas"


ISSUER_NAME = "Tu Empresa, S.L."
ISSUER_ADDRESS = "Calle Ejemplo, 1\n00000 Ciudad\nEspaña"
ISSUER_FOOTER = "info@example.com | www.example.com | IBAN: ES00 0000 0000 0000 0000 0000 | CIF: B00000000"


class QuoteTemplate(models.Model):
    name = models.CharField("nombre", max_length=120)
    intro = models.TextField("presentación", blank=True, help_text="Texto antes de la tabla (opcional). Admite {{empresa}}, {{contacto}}, {{oportunidad}}.")
    terms = models.TextField("notas", blank=True, help_text="Se imprimen al pie del presupuesto: qué incluye, condiciones, forma de pago.")
    validity_days = models.PositiveSmallIntegerField("validez (días)", default=30)
    tax_rate = models.DecimalField("IVA %", max_digits=4, decimal_places=2, default=21)
    issuer_name = models.CharField("emisor", max_length=120, default=ISSUER_NAME)
    issuer_address = models.TextField("dirección del emisor", default=ISSUER_ADDRESS)
    footer = models.CharField("pie de página", max_length=250, default=ISSUER_FOOTER, help_text="Contacto, cuenta bancaria y CIF.")
    is_default = models.BooleanField("por defecto", default=False)

    class Meta:
        ordering = ["-is_default", "name"]
        verbose_name = "plantilla de presupuesto"
        verbose_name_plural = "plantillas de presupuesto"

    def __str__(self):
        return self.name


class Quote(models.Model):
    """Presupuesto versionado. Cada versión es inmutable: editar crea la siguiente y nunca se borra ninguna."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        SENT = "sent", "Enviado"
        ACCEPTED = "accepted", "Aceptado"
        REJECTED = "rejected", "Rechazado"

    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="quotes")
    template = models.ForeignKey(QuoteTemplate, null=True, blank=True, on_delete=models.SET_NULL)
    number = models.CharField("número", max_length=20, db_index=True)
    version = models.PositiveSmallIntegerField("versión", default=1)
    title = models.CharField("descripción", max_length=200, blank=True)
    bill_to = models.TextField("dirección de facturación y de envío", blank=True)
    status = models.CharField("estado", max_length=10, choices=Status.choices, default=Status.DRAFT)
    issued_on = models.DateField("fecha", default=timezone.localdate)
    valid_until = models.DateField("válido hasta", null=True, blank=True)
    intro = models.TextField(blank=True)
    terms = models.TextField(blank=True)
    tax_rate = models.DecimalField(max_digits=4, decimal_places=2, default=21)
    lines = models.JSONField(default=list)  # foto de las líneas
    total_mrr = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_services = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_one_off = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pdf = models.FileField(upload_to="presupuestos/%Y/", blank=True)
    sent_at = models.DateTimeField("enviado", null=True, blank=True)
    sent_to = models.CharField("enviado a", max_length=300, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["number", "version"], name="uniq_quote_version")]
        verbose_name = "presupuesto"
        verbose_name_plural = "presupuestos"

    def __str__(self):
        return f"{self.number} v{self.version}"

    @property
    def base(self):
        """Base imponible: suma de los importes de las líneas, cada una en su periodo."""
        return sum((Decimal(line["total"]) for line in self.lines if line.get("unit_price") is not None), Decimal(0))

    @property
    def tax(self):
        return (self.base * self.tax_rate / 100).quantize(Decimal("0.01"))

    @property
    def total(self):
        return self.base + self.tax

    @property
    def is_mixed(self):
        """Mezcla modelos de cobro: el PDF desglosa recurrente, horas y pago único bajo el total."""
        return len({line["billing_model"] for line in self.lines}) > 1

    @property
    def breakdown(self):
        """Desglose por modelo de cobro, cada parte en su periodo: no se confunde una cuota con un pago único."""
        sums = {}
        for line in self.lines:
            if line.get("unit_price") is None:
                continue
            key = line["billing_model"] if line["billing_model"] != BillingModel.MONTHLY else f"monthly-{line.get('period', 'monthly')}"
            sums[key] = sums.get(key, Decimal(0)) + Decimal(line["total"])
        labels = {"monthly-monthly": "Cuota mensual: {} €/mes", "monthly-annual": "Cuota anual: {} €/año",
                  BillingModel.HOURLY: "Servicios por horas: {} €", BillingModel.ONE_OFF: "Pago único: {} €"}
        return [text.format(f"{sums[key]:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")) for key, text in labels.items() if key in sums]

    @property
    def is_latest(self):
        return not Quote.objects.filter(number=self.number, version__gt=self.version).exists()

    def groups(self):
        """Líneas agrupadas por modelo de cobro, con su subtotal: nunca se suman en una sola cifra."""
        labels = {
            BillingModel.MONTHLY: ("Cuotas recurrentes", self.total_mrr, "al mes"),
            BillingModel.HOURLY: ("Servicios por horas", self.total_services, ""),
            BillingModel.ONE_OFF: ("Pago único", self.total_one_off, ""),
        }
        out = []
        for key, (label, total, suffix) in labels.items():
            rows = [line for line in self.lines if line["billing_model"] == key]
            if rows:
                out.append({"key": key, "label": label, "lines": rows, "total": total, "suffix": suffix})
        return out
