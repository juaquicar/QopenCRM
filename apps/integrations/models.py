import hashlib
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

User = settings.AUTH_USER_MODEL


def hash_key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class ApiKey(models.Model):
    """Clave de API personal. Hereda los permisos del usuario; solo se guarda el hash."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField("nombre", max_length=80)
    prefix = models.CharField(max_length=12, db_index=True)
    key_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "clave de API"
        verbose_name_plural = "claves de API"

    def __str__(self):
        return f"{self.name} ({self.prefix}…)"

    @classmethod
    def issue(cls, user, name):
        raw = "qcrm_" + secrets.token_urlsafe(32)
        return cls.objects.create(user=user, name=name, prefix=raw[:10], key_hash=hash_key(raw)), raw


class Webhook(models.Model):
    EVENTS = [
        ("company.created", "Cuenta creada"),
        ("company.updated", "Cuenta modificada"),
        ("contact.created", "Contacto creado"),
        ("opportunity.created", "Oportunidad creada"),
        ("opportunity.stage_changed", "Oportunidad cambia de etapa"),
        ("opportunity.won", "Oportunidad ganada"),
        ("opportunity.lost", "Oportunidad perdida"),
        ("quote.created", "Presupuesto generado"),
        ("subscription.changed", "Suscripción: alta, cambio o baja"),
    ]

    name = models.CharField("nombre", max_length=80)
    url = models.URLField("URL")
    secret = models.CharField("secreto de firma", max_length=64, default=secrets.token_hex)
    events = models.JSONField("eventos", default=list, help_text="Lista de eventos; vacío = todos")
    is_active = models.BooleanField("activo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "webhook"
        verbose_name_plural = "webhooks"

    def __str__(self):
        return self.name


class WebhookDelivery(models.Model):
    webhook = models.ForeignKey(Webhook, on_delete=models.CASCADE, related_name="deliveries")
    event = models.CharField(max_length=40)
    payload = models.JSONField()
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    error = models.CharField(max_length=300, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "entrega de webhook"
        verbose_name_plural = "entregas de webhook"


class MailboxState(models.Model):
    """Hasta dónde se ha leído cada carpeta del buzón compartido."""

    folder = models.CharField(max_length=120, unique=True)
    uid_validity = models.BigIntegerField(default=0)
    last_uid = models.BigIntegerField(default=0)
    synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "estado del buzón"
        verbose_name_plural = "estado del buzón"

    def __str__(self):
        return self.folder


class OAuthClient(models.Model):
    """Aplicación que se conecta al MCP por OAuth (un conector de Claude, por ejemplo). Se registra sola (RFC 7591)."""

    client_id = models.CharField(max_length=80, unique=True)
    client_name = models.CharField(max_length=200, blank=True)
    redirect_uris = models.JSONField(default=list)
    token_endpoint_auth_method = models.CharField(max_length=24, default="none")
    secret_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "aplicación conectada (OAuth)"
        verbose_name_plural = "aplicaciones conectadas (OAuth)"

    def __str__(self):
        return self.client_name or self.client_id


class OAuthCode(models.Model):
    """Código de autorización: un solo uso, vida corta, atado a cliente, redirección y reto PKCE."""

    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="codes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    redirect_uri = models.CharField(max_length=500)
    code_challenge = models.CharField(max_length=128)
    resource = models.CharField(max_length=300, blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class OAuthToken(models.Model):
    """Acceso concedido por una persona a una aplicación. Solo se guardan los hashes de los tokens."""

    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="tokens")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="oauth_tokens")
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    resource = models.CharField(max_length=300, blank=True)
    access_expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "acceso OAuth"
        verbose_name_plural = "accesos OAuth"


class MicrosoftConfig(models.Model):
    """Datos de la aplicación registrada en Microsoft Entra para crear reuniones de Teams. Una sola fila."""

    tenant_id = models.CharField("id del directorio (tenant)", max_length=80, blank=True)
    client_id = models.CharField("id de la aplicación (cliente)", max_length=80, blank=True)
    client_secret_enc = models.TextField(blank=True, editable=False)  # cifrado; nunca en claro
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "configuración de Microsoft 365"
        verbose_name_plural = "configuración de Microsoft 365"

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def ready(self):
        return bool(self.tenant_id and self.client_id and self.client_secret_enc)


class MicrosoftAccount(models.Model):
    """Cuenta de Microsoft 365 conectada para convocar reuniones de Teams en su calendario."""

    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="microsoft_accounts")
    email = models.CharField(max_length=200)
    name = models.CharField(max_length=200, blank=True)
    shared = models.BooleanField("compartida con el equipo", default=False)
    refresh_token_enc = models.TextField(blank=True, editable=False)  # cifrado; vacío = buzón con permisos de aplicación
    connected_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-shared", "email"]
        constraints = [models.UniqueConstraint(fields=["owner", "email"], name="uniq_ms_account_per_owner")]
        verbose_name = "cuenta de Microsoft 365"
        verbose_name_plural = "cuentas de Microsoft 365"

    def __str__(self):
        return f"{self.email}{' (compartida)' if self.shared else ''}"

    @staticmethod
    def usable_by(user):
        """Las compartidas con el equipo y las que ha conectado la propia persona."""
        return MicrosoftAccount.objects.filter(models.Q(shared=True) | models.Q(owner=user))
