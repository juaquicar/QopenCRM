from django.db import models


class BillingModel(models.TextChoices):
    MONTHLY = "monthly", "Recurrente"
    HOURLY = "hourly", "Por hora"
    ONE_OFF = "one_off", "Pago único"


class ProductFamily(models.Model):
    """Línea de producto (Plataforma, Servicios, Proyectos…). Agrupa los planes por niveles."""

    name = models.CharField("nombre", max_length=80, unique=True)
    order = models.PositiveSmallIntegerField("orden", default=0)
    color = models.CharField("color", max_length=9, default="#3aa0ff")

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "familia"
        verbose_name_plural = "familias"

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Activo"
        DRAFT = "draft", "Borrador"
        RETIRED = "retired", "Retirado"

    family = models.ForeignKey(ProductFamily, verbose_name="familia", null=True, blank=True, on_delete=models.SET_NULL, related_name="products")
    code = models.CharField("código", max_length=40, unique=True)
    name = models.CharField("nombre", max_length=120)
    description = models.TextField("descripción", blank=True)
    billing_model = models.CharField("modelo de cobro", max_length=10, choices=BillingModel.choices, default=BillingModel.MONTHLY)
    unit = models.CharField("unidad", max_length=30, default="ud", help_text="mes, usuario/mes, hora, proyecto…")
    # precio nulo permitido: productos sin tarifa cerrada (Elektra, Gass, Hydro)
    price = models.DecimalField("precio", max_digits=12, decimal_places=2, null=True, blank=True)
    price_annual = models.DecimalField("precio anual", max_digits=12, decimal_places=2, null=True, blank=True, help_text="Solo recurrentes con tarifa anual")
    tier_level = models.PositiveSmallIntegerField("nivel de plan", null=True, blank=True, help_text="1 Lite · 2 Basic · 3 Plus · 4 Pro")
    is_addon = models.BooleanField("extra (p. ej. usuario adicional)", default=False)
    status = models.CharField("estado", max_length=8, choices=Status.choices, default=Status.ACTIVE)
    external_id = models.CharField("id externo", max_length=120, blank=True, db_index=True)
    # precios que manda Stripe: cuota base mensual/anual en price/price_annual
    # y precio por usuario según cuántos haya, en tramos: [{"up_to": 2, "month": 24.95, "year": 269.46}, …]
    stripe_product_id = models.CharField("producto en Stripe", max_length=60, blank=True, db_index=True)
    user_tiers = models.JSONField("precio por usuario (Stripe)", default=list, blank=True)
    stripe_synced_at = models.DateTimeField("precios de Stripe a", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["family__order", "family__name", "tier_level", "name"]
        verbose_name = "producto"
        verbose_name_plural = "productos"

    def __str__(self):
        return self.name

    @property
    def has_price(self):
        return self.price is not None
