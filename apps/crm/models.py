import re
import unicodedata
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from apps.catalog.models import BillingModel, Product

User = settings.AUTH_USER_MODEL
HOLD_TASK = "Revisar oportunidad parada"

_LEGAL = re.compile(r"\b(s\.?l\.?u?\.?|s\.?a\.?u?\.?|s\.?c\.?|s\.?coop\.?|sociedad limitada|sociedad anonima|ltd|inc|gmbh)\b")


def normalize_name(value):
    """Clave para deduplicar cuentas: sin tildes, sin forma jurídica, sin signos."""
    text = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode().lower()
    text = _LEGAL.sub(" ", text)
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_domain(value):
    value = (value or "").strip().lower()
    value = re.sub(r"^https?://", "", value).split("/")[0]
    return re.sub(r"^www\.", "", value)


class Stamped(models.Model):
    created_at = models.DateTimeField("creado", auto_now_add=True)
    updated_at = models.DateTimeField("modificado", auto_now=True)

    class Meta:
        abstract = True


class Event(Stamped):
    """Feria o evento en el que se conoce a empresas. Sirve para medir qué sale de cada uno."""

    class Kind(models.TextChoices):
        FAIR = "fair", "Feria"
        EVENT = "event", "Evento"
        WEBINAR = "webinar", "Webinar"
        OTHER = "other", "Otro"

    name = models.CharField("nombre", max_length=160, unique=True)
    kind = models.CharField("tipo", max_length=8, choices=Kind.choices, default=Kind.FAIR)
    starts_on = models.DateField("empieza", null=True, blank=True)
    ends_on = models.DateField("termina", null=True, blank=True)
    place = models.CharField("lugar", max_length=160, blank=True)
    website = models.CharField("web", max_length=200, blank=True)
    notes = models.TextField("notas", blank=True)
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)

    class Meta:
        ordering = ["-starts_on", "name"]
        verbose_name = "feria o evento"
        verbose_name_plural = "ferias y eventos"

    def __str__(self):
        return self.name


class Company(Stamped):
    """Cuenta: ficha única de la empresa u organismo."""

    class Kind(models.TextChoices):
        PROSPECT = "prospect", "Potencial"
        CUSTOMER = "customer", "Cliente"
        PARTNER = "partner", "Partner"
        FORMER = "former", "Antiguo cliente"

    name = models.CharField("nombre", max_length=200)
    name_key = models.CharField(max_length=200, db_index=True, editable=False)
    kind = models.CharField("tipo", max_length=10, choices=Kind.choices, default=Kind.PROSPECT)
    tax_id = models.CharField("CIF/NIF", max_length=20, blank=True, db_index=True)
    website = models.CharField("web", max_length=200, blank=True)
    domain = models.CharField(max_length=120, blank=True, db_index=True, editable=False)
    email = models.EmailField("correo", blank=True)
    phone = models.CharField("teléfono", max_length=40, blank=True)
    address = models.CharField("dirección", max_length=200, blank=True)
    city = models.CharField("población", max_length=80, blank=True)
    province = models.CharField("provincia", max_length=80, blank=True)
    country = models.CharField("país", max_length=60, blank=True, default="España")
    sector = models.CharField("sector", max_length=80, blank=True)
    event = models.ForeignKey(Event, verbose_name="conocida en (feria o evento)", null=True, blank=True, on_delete=models.SET_NULL, related_name="companies")
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)  # id en el sistema de origen, para importaciones repetibles
    notes = models.TextField("notas", blank=True)
    owner = models.ForeignKey(User, verbose_name="responsable", null=True, blank=True, on_delete=models.SET_NULL, related_name="companies")

    class Meta:
        ordering = ["name"]
        verbose_name = "cuenta"
        verbose_name_plural = "cuentas"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        self.name_key = normalize_name(self.name)
        self.domain = normalize_domain(self.website) or (self.email.split("@")[1].lower() if "@" in self.email else "")
        self.tax_id = re.sub(r"[\s.\-]", "", self.tax_id or "").upper()
        super().save(*args, **kwargs)

    def duplicates(self):
        """Otras cuentas que parecen la misma (nombre, CIF o dominio)."""
        cond = Q(name_key=self.name_key) if self.name_key else Q(pk__in=[])
        if self.tax_id:
            cond |= Q(tax_id=self.tax_id)
        if self.domain and self.domain not in GENERIC_DOMAINS:
            cond |= Q(domain=self.domain)
        return Company.objects.filter(cond).exclude(pk=self.pk)


GENERIC_DOMAINS = {"gmail.com", "hotmail.com", "outlook.com", "yahoo.es", "yahoo.com", "icloud.com", "live.com", "telefonica.net"}


class Contact(Stamped):
    """Persona asociada a una cuenta, con su base legal RGPD."""

    class LegalBasis(models.TextChoices):
        CONSENT = "consent", "Consentimiento"
        CONTRACT = "contract", "Relación contractual"
        LEGITIMATE = "legitimate", "Interés legítimo"
        UNKNOWN = "unknown", "Sin determinar"

    company = models.ForeignKey(Company, verbose_name="empresa", null=True, blank=True, on_delete=models.CASCADE, related_name="contacts")
    first_name = models.CharField("nombre", max_length=80)
    last_name = models.CharField("apellidos", max_length=120, blank=True)
    position = models.CharField("cargo", max_length=120, blank=True)
    email = models.EmailField("correo", blank=True, db_index=True)
    phone = models.CharField("teléfono", max_length=40, blank=True)
    mobile = models.CharField("móvil", max_length=40, blank=True)
    is_primary = models.BooleanField("contacto principal", default=False)
    notes = models.TextField("notas", blank=True)
    owner = models.ForeignKey(User, verbose_name="responsable", null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts")
    # RGPD
    legal_basis = models.CharField("base legal", max_length=12, choices=LegalBasis.choices, default=LegalBasis.UNKNOWN)
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)  # id en el sistema de origen, para importaciones repetibles
    consent_marketing = models.BooleanField("acepta comunicaciones comerciales", default=False)
    consent_at = models.DateTimeField("fecha del consentimiento", null=True, blank=True)
    consent_source = models.CharField("origen del consentimiento", max_length=200, blank=True)
    erased_at = models.DateTimeField("suprimido el", null=True, blank=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        verbose_name = "contacto"
        verbose_name_plural = "contactos"

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    def save(self, *args, **kwargs):
        self.email = (self.email or "").strip().lower()
        super().save(*args, **kwargs)

    def erase(self):
        """Derecho de supresión: se anonimiza la persona y se conserva el histórico agregado."""
        self.first_name, self.last_name = "Contacto", "suprimido"
        self.email = self.phone = self.mobile = self.position = self.notes = self.consent_source = ""
        self.consent_marketing = False
        self.erased_at = timezone.now()
        self.save()
        self.activities.update(body="", subject="(contenido suprimido por RGPD)", contact=None)


class Stage(models.Model):
    """Etapa del pipeline, configurable."""

    name = models.CharField("nombre", max_length=60)
    order = models.PositiveSmallIntegerField("orden", default=0)
    probability = models.PositiveSmallIntegerField("probabilidad %", default=10)
    is_won = models.BooleanField("ganada", default=False)
    is_lost = models.BooleanField("perdida", default=False)
    color = models.CharField("color", max_length=9, default="#3aa0ff")

    class Meta:
        ordering = ["order"]
        verbose_name = "etapa"
        verbose_name_plural = "etapas"

    def __str__(self):
        return self.name

    @property
    def is_open(self):
        return not (self.is_won or self.is_lost)


class LossReason(models.Model):
    name = models.CharField("motivo", max_length=80, unique=True)
    order = models.PositiveSmallIntegerField("orden", default=0)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "motivo de pérdida"
        verbose_name_plural = "motivos de pérdida"

    def __str__(self):
        return self.name


class Opportunity(Stamped):
    class Priority(models.IntegerChoices):
        LOW = 1, "Baja"
        NORMAL = 2, "Normal"
        HIGH = 3, "Alta"

    title = models.CharField("título", max_length=200)
    company = models.ForeignKey(Company, verbose_name="empresa", on_delete=models.CASCADE, related_name="opportunities")
    contact = models.ForeignKey(Contact, verbose_name="contacto", null=True, blank=True, on_delete=models.SET_NULL, related_name="opportunities")
    stage = models.ForeignKey(Stage, verbose_name="etapa", on_delete=models.PROTECT, related_name="opportunities")
    owner = models.ForeignKey(User, verbose_name="responsable", null=True, blank=True, on_delete=models.SET_NULL, related_name="opportunities")
    assignees = models.ManyToManyField(User, verbose_name="también asignada a", blank=True, related_name="assigned_opportunities")
    priority = models.PositiveSmallIntegerField("prioridad", choices=Priority.choices, default=Priority.NORMAL)
    probability = models.PositiveSmallIntegerField("probabilidad %", default=10)
    expected_close = models.DateField("cierre estimado", null=True, blank=True)
    source = models.CharField("origen", max_length=80, blank=True)
    description = models.TextField("descripción", blank=True)
    loss_reason = models.ForeignKey(LossReason, verbose_name="motivo de pérdida", null=True, blank=True, on_delete=models.SET_NULL)
    loss_notes = models.CharField("detalle de la pérdida", max_length=300, blank=True)
    closed_at = models.DateTimeField("cerrada el", null=True, blank=True)
    position = models.PositiveIntegerField(default=0)  # orden dentro de la columna
    # Importes separados por modelo de cobro; se recalculan desde las líneas
    amount_mrr = models.DecimalField("MRR", max_digits=12, decimal_places=2, default=0)
    amount_services = models.DecimalField("servicios por hora", max_digits=12, decimal_places=2, default=0)
    amount_one_off = models.DecimalField("pago único", max_digits=12, decimal_places=2, default=0)
    # Próxima acción: fecha de la primera tarea abierta (se mantiene desde Task)
    next_action_at = models.DateTimeField("próxima acción", null=True, blank=True, db_index=True)
    # parada por el cliente: conserva su etapa, pero obliga a revisarla en una fecha
    on_hold_until = models.DateField("revisar el", null=True, blank=True, db_index=True)
    on_hold_reason = models.CharField("motivo de la parada", max_length=200, blank=True)
    on_hold_since = models.DateField("parada desde", null=True, blank=True)
    hold_count = models.PositiveSmallIntegerField("veces parada", default=0)
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)  # id en el sistema de origen, para importaciones repetibles

    class Meta:
        ordering = ["position", "-updated_at"]
        verbose_name = "oportunidad"
        verbose_name_plural = "oportunidades"

    def __str__(self):
        return self.title

    @property
    def is_open(self):
        return self.stage.is_open

    MAX_HOLD_DAYS = 90

    @property
    def on_hold(self):
        return bool(self.on_hold_until) and self.stage.is_open

    @property
    def hold_overdue(self):
        return self.on_hold and self.on_hold_until < timezone.localdate()

    def hold(self, until, reason, user=None):
        """Para la oportunidad a la espera del cliente. La revisión es obligatoria y pasa a ser su próxima acción."""
        today = timezone.localdate()
        if not self.stage.is_open:
            raise ValueError("Solo se puede parar una oportunidad abierta.")
        if until <= today:
            raise ValueError("La fecha de revisión tiene que ser posterior a hoy.")
        if (until - today).days > self.MAX_HOLD_DAYS:
            raise ValueError(f"Como máximo se puede parar {self.MAX_HOLD_DAYS} días: revisa antes del {today + timedelta(days=self.MAX_HOLD_DAYS):%d/%m/%Y}.")
        if not self.on_hold_until:
            self.on_hold_since = today
        self.on_hold_until, self.on_hold_reason = until, reason[:200]
        self.hold_count += 1
        self.save(update_fields=["on_hold_until", "on_hold_reason", "on_hold_since", "hold_count", "updated_at"])
        # la revisión sustituye a la anterior, si la había, para que solo haya una pendiente
        self.tasks.filter(done_at__isnull=True, title__startswith=HOLD_TASK).delete()
        Task.objects.create(
            title=f"{HOLD_TASK}: {self.title}"[:200], kind=Task.Kind.TODO, opportunity=self, company=self.company,
            assignee=self.owner or user, created_by=user, notes=reason,
            due_at=timezone.make_aware(datetime.combine(until, time(9, 0))),
        )
        Activity.objects.create(company=self.company, opportunity=self, user=user, kind=Activity.Kind.SYSTEM,
                                subject=f"Parada por el cliente hasta el {until:%d/%m/%Y}", body=reason)

    def resume(self, user=None, note=""):
        if not self.on_hold_until:
            return
        self.on_hold_until, self.on_hold_reason, self.on_hold_since = None, "", None
        self.save(update_fields=["on_hold_until", "on_hold_reason", "on_hold_since", "updated_at"])
        self.tasks.filter(done_at__isnull=True, title__startswith=HOLD_TASK).update(done_at=timezone.now())
        self.refresh_next_action()
        Activity.objects.create(company=self.company, opportunity=self, user=user, kind=Activity.Kind.SYSTEM, subject="Reanudada", body=note)

    @property
    def first_year_value(self):
        """Valor del primer año: 12 meses de recurrente más servicios y pago único."""
        return self.amount_mrr * 12 + self.amount_services + self.amount_one_off

    @property
    def weighted_value(self):
        return self.first_year_value * self.probability / 100

    @property
    def next_action(self):
        return self.tasks.filter(done_at__isnull=True).order_by("due_at").first()

    def recalc(self, save=True):
        totals = {BillingModel.MONTHLY: Decimal(0), BillingModel.HOURLY: Decimal(0), BillingModel.ONE_OFF: Decimal(0)}
        for line in self.lines.all():
            totals[line.billing_model] += line.monthly_total if line.billing_model == BillingModel.MONTHLY else line.total
        self.amount_mrr = totals[BillingModel.MONTHLY]
        self.amount_services = totals[BillingModel.HOURLY]
        self.amount_one_off = totals[BillingModel.ONE_OFF]
        if save:
            Opportunity.objects.filter(pk=self.pk).update(
                amount_mrr=self.amount_mrr, amount_services=self.amount_services, amount_one_off=self.amount_one_off
            )

    def refresh_next_action(self):
        task = self.next_action
        self.next_action_at = task.due_at if task else None
        Opportunity.objects.filter(pk=self.pk).update(next_action_at=self.next_action_at)

    def move_to(self, stage, user=None, loss_reason=None, loss_notes=""):
        """Cambia de etapa dejando rastro en el timeline. Devuelve True si acaba de ganarse."""
        previous = self.stage
        if previous == stage:
            return False
        self.stage = stage
        self.probability = stage.probability
        if stage.is_open:
            self.closed_at, self.loss_reason, self.loss_notes = None, None, ""
        else:
            self.closed_at = timezone.now()
            self.on_hold_until, self.on_hold_reason, self.on_hold_since = None, "", None  # cerrada: deja de estar parada
            if stage.is_lost:
                self.loss_reason, self.loss_notes = loss_reason, loss_notes
        self.save()
        Activity.objects.create(
            company=self.company, opportunity=self, user=user, kind=Activity.Kind.STAGE,
            subject=f"{previous.name} → {stage.name}",
            body=(f"Motivo: {loss_reason}. {loss_notes}".strip() if stage.is_lost and loss_reason else ""),
        )
        return stage.is_won and not previous.is_won


class OpportunityLine(models.Model):
    class Period(models.TextChoices):
        MONTHLY = "monthly", "Mensual"
        ANNUAL = "annual", "Anual"

    opportunity = models.ForeignKey(Opportunity, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, verbose_name="producto", null=True, blank=True, on_delete=models.PROTECT, related_name="opportunity_lines")
    description = models.CharField("concepto", max_length=200, blank=True)
    billing_model = models.CharField("cobro", max_length=10, choices=BillingModel.choices)
    period = models.CharField("periodo", max_length=8, choices=Period.choices, default=Period.MONTHLY)
    quantity = models.DecimalField("cantidad", max_digits=10, decimal_places=2, default=1)
    unit = models.CharField("unidad", max_length=30, blank=True)
    unit_price = models.DecimalField("precio", max_digits=12, decimal_places=2, null=True, blank=True)
    discount = models.DecimalField("dto. %", max_digits=5, decimal_places=2, default=0)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "línea"
        verbose_name_plural = "líneas"

    def __str__(self):
        return self.description or (self.product.name if self.product else "Línea")

    @property
    def total(self):
        """Importe de la línea en su propio periodo (mes, año, horas o pago único)."""
        if self.unit_price is None:
            return Decimal(0)
        return (self.quantity * self.unit_price * (100 - self.discount) / 100).quantize(Decimal("0.01"))

    @property
    def monthly_total(self):
        if self.billing_model != BillingModel.MONTHLY:
            return Decimal(0)
        return (self.total / 12).quantize(Decimal("0.01")) if self.period == self.Period.ANNUAL else self.total

    def save(self, *args, **kwargs):
        if self.product_id:
            self.billing_model = self.billing_model or self.product.billing_model
            self.description = self.description or self.product.name
            self.unit = self.unit or self.product.unit
        super().save(*args, **kwargs)
        self.opportunity.recalc()

    def delete(self, *args, **kwargs):
        opportunity = self.opportunity
        result = super().delete(*args, **kwargs)
        opportunity.recalc()
        return result


class Activity(models.Model):
    """Entrada del timeline de una cuenta: correo, llamada, reunión, nota o cambio de etapa."""

    class Kind(models.TextChoices):
        NOTE = "note", "Nota"
        CALL = "call", "Llamada"
        EMAIL = "email", "Correo"
        MEETING = "meeting", "Reunión"
        STAGE = "stage", "Cambio de etapa"
        SYSTEM = "system", "Sistema"

    class Direction(models.TextChoices):
        NONE = "", "—"
        IN = "in", "Entrante"
        OUT = "out", "Saliente"

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="activities")
    contact = models.ForeignKey(Contact, verbose_name="contacto", null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    opportunity = models.ForeignKey(Opportunity, verbose_name="oportunidad", null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="activities")
    kind = models.CharField("tipo", max_length=8, choices=Kind.choices, default=Kind.NOTE)
    direction = models.CharField(max_length=3, choices=Direction.choices, blank=True, default="")
    subject = models.CharField("asunto", max_length=250, blank=True)
    body = models.TextField("detalle", blank=True)
    occurred_at = models.DateTimeField("fecha", default=timezone.now, db_index=True)
    # captura automática: identificador del correo o evento de origen, para no duplicar
    source = models.CharField(max_length=20, blank=True)
    external_id = models.CharField(max_length=255, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-occurred_at", "-id"]
        verbose_name = "actividad"
        verbose_name_plural = "actividades"
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id", "company"], condition=~Q(external_id=""), name="uniq_activity_external"),
        ]

    def __str__(self):
        return self.subject or self.get_kind_display()


class Task(Stamped):
    """Tarea o recordatorio. La primera abierta de una oportunidad es su próxima acción."""

    class Kind(models.TextChoices):
        TODO = "todo", "Tarea"
        CALL = "call", "Llamar"
        EMAIL = "email", "Escribir"
        MEETING = "meeting", "Reunión"
        QUOTE = "quote", "Enviar presupuesto"

    title = models.CharField("qué hay que hacer", max_length=200)
    kind = models.CharField("tipo", max_length=8, choices=Kind.choices, default=Kind.TODO)
    due_at = models.DateTimeField("cuándo", db_index=True)
    assignee = models.ForeignKey(User, verbose_name="responsable", on_delete=models.CASCADE, related_name="tasks")
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_tasks")
    company = models.ForeignKey(Company, verbose_name="empresa", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    opportunity = models.ForeignKey(Opportunity, verbose_name="oportunidad", null=True, blank=True, on_delete=models.CASCADE, related_name="tasks")
    notes = models.TextField("notas", blank=True)
    done_at = models.DateTimeField("hecha el", null=True, blank=True)
    reminded_at = models.DateTimeField(null=True, blank=True)
    external_id = models.CharField(max_length=120, blank=True, db_index=True, editable=False)  # id en el sistema de origen, para importaciones repetibles

    class Meta:
        ordering = ["due_at"]
        verbose_name = "tarea"
        verbose_name_plural = "tareas"

    def __str__(self):
        return self.title

    @property
    def is_done(self):
        return self.done_at is not None

    @property
    def is_overdue(self):
        return not self.done_at and self.due_at < timezone.now()

    def save(self, *args, **kwargs):
        if self.opportunity_id and not self.company_id:
            self.company_id = self.opportunity.company_id
        super().save(*args, **kwargs)
        if self.opportunity_id:
            self.opportunity.refresh_next_action()

    def delete(self, *args, **kwargs):
        opportunity = self.opportunity
        result = super().delete(*args, **kwargs)
        if opportunity:
            opportunity.refresh_next_action()
        return result

    def complete(self, user=None):
        self.done_at = timezone.now()
        self.save()
        if self.company_id:
            Activity.objects.create(
                company_id=self.company_id, opportunity=self.opportunity, user=user,
                kind=Activity.Kind.SYSTEM, subject=f"Hecho: {self.title}",
            )
