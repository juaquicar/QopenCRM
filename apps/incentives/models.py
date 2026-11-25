"""Incentivos del equipo, con tres programas de ejemplo que cada empresa puede adaptar (documéntalos en el Banco de conocimiento):

- Ventas, cliente o proyecto nuevo: 5 % del margen (facturación − costes directos), pagadero en proporción a lo cobrado.
- Ventas, cuota recurrente: la media de las cuatro primeras cuotas, una vez cobrada la cuarta.
- Project Managers: 5 % del margen si se cumplen satisfacción y rentabilidad, +1 % por plazos y +1 % por costes.
- Productividad y crecimiento: fondo anual del 10 % del beneficio si crece al menos un 15 %, repartido según la nota de cada persona.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.db import models

from apps.crm.models import Company, Opportunity

User = settings.AUTH_USER_MODEL
CENT = Decimal("0.01")
ZERO = Decimal(0)


def money(value):
    """Al céntimo, con redondeo comercial (el medio céntimo sube), como en los ejemplos de los programas."""
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


class Incentive(models.Model):
    class Program(models.TextChoices):
        SALES_NEW = "sales_new", "Ventas · cliente o proyecto nuevo"
        SALES_QUOTA = "sales_quota", "Ventas · cuota recurrente"
        PM = "pm", "Project Manager"

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Pendiente de validar"
        APPROVED = "approved", "Aprobado"
        REJECTED = "rejected", "No procede"

    SALES_RATE = Decimal(5)
    PM_BASE_RATE, PM_EXTRA_RATE, PM_MIN_SATISFACTION = Decimal(5), Decimal(1), Decimal(8)

    program = models.CharField("programa", max_length=12, choices=Program.choices)
    user = models.ForeignKey(User, verbose_name="persona", on_delete=models.CASCADE, related_name="incentives")
    concept = models.CharField("concepto", max_length=200, help_text="Cliente y proyecto o servicio.")
    company = models.ForeignKey(Company, verbose_name="empresa", null=True, blank=True, on_delete=models.SET_NULL, related_name="incentives")
    opportunity = models.ForeignKey(Opportunity, verbose_name="oportunidad", null=True, blank=True, on_delete=models.SET_NULL, related_name="incentives")
    status = models.CharField("estado", max_length=10, choices=Status.choices, default=Status.PROPOSED)
    notes = models.TextField("notas", blank=True)
    # margen: ventas (cliente nuevo) y PM
    revenue = models.DecimalField("facturación acordada", max_digits=12, decimal_places=2, default=0, help_text="Sin IVA.")
    direct_costs = models.DecimalField("costes directos", max_digits=12, decimal_places=2, default=0, help_text="Personal asignado, licencias, materiales.")
    collected = models.DecimalField("cobrado al cliente", max_digits=12, decimal_places=2, default=0, help_text="El incentivo se paga en proporción a lo cobrado.")
    # PM: indicadores de cumple / no cumple
    planned_margin = models.DecimalField("margen previsto", max_digits=12, decimal_places=2, null=True, blank=True)
    satisfaction_score = models.DecimalField("nota de satisfacción del cliente", max_digits=4, decimal_places=2, null=True, blank=True, help_text="De 0 a 10. Cumple con 8 o más.")
    kpi_deadlines = models.BooleanField("cumple plazos", default=False, help_text="Desviación del cronograma de hasta el 10 %.")
    kpi_costs = models.BooleanField("cumple costes", default=False, help_text="Dentro del ±5 % del presupuesto.")
    # cuota recurrente
    quota_1 = models.DecimalField("1.ª cuota", max_digits=10, decimal_places=2, null=True, blank=True)
    quota_2 = models.DecimalField("2.ª cuota", max_digits=10, decimal_places=2, null=True, blank=True)
    quota_3 = models.DecimalField("3.ª cuota", max_digits=10, decimal_places=2, null=True, blank=True)
    quota_4 = models.DecimalField("4.ª cuota", max_digits=10, decimal_places=2, null=True, blank=True)
    quotas_collected = models.PositiveSmallIntegerField("cuotas cobradas", default=0, help_text="El incentivo nace al cobrar la cuarta.")
    # pago al empleado
    paid = models.DecimalField("abonado a la persona", max_digits=12, decimal_places=2, default=0)
    paid_on = models.DateField("fecha del último abono", null=True, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    decided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    decided_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "incentivo"
        verbose_name_plural = "incentivos"
        constraints = [models.UniqueConstraint(fields=["opportunity", "program", "user"], condition=models.Q(opportunity__isnull=False), name="uniq_incentive_per_deal")]

    def __str__(self):
        return f"{self.get_program_display()} · {self.concept}"

    # ---- cálculo
    @property
    def margin(self):
        return max(self.revenue - self.direct_costs, ZERO)

    @property
    def quotas(self):
        return [q for q in (self.quota_1, self.quota_2, self.quota_3, self.quota_4) if q is not None]

    @property
    def kpi_satisfaction(self):
        return self.satisfaction_score is not None and self.satisfaction_score >= self.PM_MIN_SATISFACTION

    @property
    def kpi_profitability(self):
        return self.planned_margin is not None and self.revenue - self.direct_costs >= self.planned_margin

    @property
    def rate(self):
        """Porcentaje sobre el margen. En PM, sin satisfacción y rentabilidad no hay incentivo."""
        if self.program == self.Program.SALES_NEW:
            return self.SALES_RATE
        if self.program == self.Program.PM:
            if not (self.kpi_satisfaction and self.kpi_profitability):
                return ZERO
            return self.PM_BASE_RATE + self.PM_EXTRA_RATE * (self.kpi_deadlines + self.kpi_costs)
        return ZERO

    @property
    def total(self):
        """Incentivo completo si todo se cobra (en cuota recurrente, media de las cuotas conocidas)."""
        if self.program == self.Program.SALES_QUOTA:
            return money(sum(self.quotas, ZERO) / len(self.quotas)) if self.quotas else ZERO
        return money(self.margin * self.rate / 100)

    @property
    def collected_pct(self):
        if self.program == self.Program.SALES_QUOTA:
            return Decimal(min(self.quotas_collected, 4) * 25)
        return (min(self.collected / self.revenue, Decimal(1)) * 100).quantize(Decimal("0.1")) if self.revenue else ZERO

    @property
    def earned(self):
        """Lo que ya corresponde pagar: en proporción a lo cobrado; en cuota recurrente, todo al cobrar la cuarta cuota."""
        if self.status != self.Status.APPROVED:
            return ZERO
        if self.program == self.Program.SALES_QUOTA:
            return self.total if self.quotas_collected >= 4 and len(self.quotas) == 4 else ZERO
        return money(self.total * min(self.collected / self.revenue, Decimal(1))) if self.revenue else ZERO

    @property
    def due(self):
        return max(self.earned - self.paid, ZERO)

    @property
    def kpis(self):
        return [("Satisfacción", self.kpi_satisfaction, True), ("Rentabilidad", self.kpi_profitability, True),
                ("Plazos", self.kpi_deadlines, False), ("Costes", self.kpi_costs, False)]


class ProductivityYear(models.Model):
    """Ejercicio del plan global de productividad: de aquí sale el fondo a repartir."""

    class Decision(models.TextChoices):
        AUTO = "auto", "Según la regla (crecimiento ≥ umbral)"
        APPLY = "apply", "Aplicar aunque no llegue al umbral"
        CANCEL = "cancel", "No aplicar este año"

    year = models.PositiveSmallIntegerField("ejercicio", unique=True)
    profit = models.DecimalField("beneficio del ejercicio", max_digits=14, decimal_places=2, default=0)
    previous_profit = models.DecimalField("beneficio del ejercicio anterior", max_digits=14, decimal_places=2, default=0)
    fund_pct = models.DecimalField("fondo: % del beneficio", max_digits=5, decimal_places=2, default=10)
    growth_threshold = models.DecimalField("crecimiento mínimo %", max_digits=5, decimal_places=2, default=15)
    peer_weight = models.DecimalField("peso de la nota de compañeros %", max_digits=5, decimal_places=2, default=50, help_text="El resto es la nota de la dirección.")
    decision = models.CharField("decisión de la dirección", max_length=6, choices=Decision.choices, default=Decision.AUTO)
    fund_override = models.DecimalField("fondo fijado a mano", max_digits=14, decimal_places=2, null=True, blank=True, help_text="Solo si la dirección decide reducirlo.")
    published = models.BooleanField("visible para el equipo", default=False, help_text="Hasta publicarlo, solo lo ve quien gestiona incentivos.")
    notes = models.TextField("notas", blank=True)

    class Meta:
        ordering = ["-year"]
        verbose_name = "ejercicio de productividad"
        verbose_name_plural = "ejercicios de productividad"

    def __str__(self):
        return f"Productividad {self.year}"

    @property
    def growth(self):
        if self.previous_profit <= 0:
            return None
        return ((self.profit - self.previous_profit) / self.previous_profit * 100).quantize(Decimal("0.1"))

    @property
    def activated(self):
        if self.decision == self.Decision.CANCEL or self.profit <= 0:
            return False
        if self.decision == self.Decision.APPLY:
            return True
        return self.growth is not None and self.growth >= self.growth_threshold

    @property
    def fund(self):
        if not self.activated:
            return ZERO
        return self.fund_override if self.fund_override is not None else money(self.profit * self.fund_pct / 100)

    def shares(self):
        """Reparto: cada persona recibe el fondo en proporción a su nota, ajustada a los meses trabajados."""
        scores = list(self.scores.select_related("user"))
        total = sum((score.weight for score in scores), ZERO)
        for score in scores:
            score.share = money(self.fund * score.weight / total) if total else ZERO
        return scores


class ProductivityScore(models.Model):
    year = models.ForeignKey(ProductivityYear, on_delete=models.CASCADE, related_name="scores")
    user = models.ForeignKey(User, verbose_name="persona", on_delete=models.CASCADE, related_name="productivity_scores")
    peer_score = models.DecimalField("nota de compañeros", max_digits=4, decimal_places=2, default=0, help_text="De 0 a 10.")
    management_score = models.DecimalField("nota de la dirección", max_digits=4, decimal_places=2, default=0, help_text="De 0 a 10.")
    months = models.PositiveSmallIntegerField("meses trabajados en el ejercicio", default=12)
    eligible = models.BooleanField("participa", default=True, help_text="Desmarca si es directivo, ha causado baja o no llega al mínimo de permanencia.")

    class Meta:
        ordering = ["user__name"]
        constraints = [models.UniqueConstraint(fields=["year", "user"], name="uniq_score_per_year")]
        verbose_name = "nota de productividad"
        verbose_name_plural = "notas de productividad"

    def __str__(self):
        return f"{self.user} · {self.year.year}"

    @property
    def final(self):
        weight = Decimal(self.year.peer_weight) / 100
        return money(Decimal(self.peer_score) * weight + Decimal(self.management_score) * (1 - weight))

    @property
    def weight(self):
        return self.final * min(self.months, 12) / 12 if self.eligible else ZERO
