import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.utils import timezone


class Role(models.Model):
    """Rol comercial. El superusuario ajusta el alcance de cada uno desde Ajustes."""

    class Scope(models.TextChoices):
        OWN = "own", "Solo lo suyo"
        ALL = "all", "Todo"

    code = models.SlugField("código", unique=True)
    name = models.CharField("nombre", max_length=60)
    order = models.PositiveSmallIntegerField("orden", default=0)
    view_scope = models.CharField("ve", max_length=4, choices=Scope.choices, default=Scope.OWN)
    edit_scope = models.CharField("edita", max_length=4, choices=Scope.choices, default=Scope.OWN)
    can_delete = models.BooleanField("puede borrar", default=False)
    can_export = models.BooleanField("puede exportar", default=False)
    can_manage_catalog = models.BooleanField("gestiona productos", default=False)
    can_manage_pipeline = models.BooleanField("configura etapas y plantillas", default=False)
    can_manage_events = models.BooleanField("gestiona ferias y eventos", default=False)
    can_manage_knowledge = models.BooleanField("edita el banco de conocimiento", default=False)
    can_manage_incentives = models.BooleanField("gestiona incentivos", default=False)
    can_use_api = models.BooleanField("puede usar API y MCP", default=True)
    mcp_tools = models.JSONField("herramientas MCP", default=list, blank=True,
                                 help_text="Vacío = todas las que el rol permite. Si marcas alguna, el asistente solo ofrecerá esas.")

    class Meta:
        ordering = ["order"]
        verbose_name = "rol"
        verbose_name_plural = "roles"

    def __str__(self):
        return self.name


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, name="", role=None, **extra):
        if not email:
            raise ValueError("El correo es obligatorio")
        user = self.model(email=self.normalize_email(email).lower(), name=name, role=role, **extra)
        user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, name="", **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        user = self.create_user(email, name=name, **extra)
        if password:
            user.set_password(password)
            user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField("correo", unique=True)
    name = models.CharField("nombre", max_length=120, blank=True)
    role = models.ForeignKey(Role, verbose_name="rol", null=True, blank=True, on_delete=models.PROTECT, related_name="users")
    is_active = models.BooleanField("activo", default=True)
    is_staff = models.BooleanField("acceso a administración", default=False)
    task_emails = models.BooleanField("recibe recordatorios por correo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        ordering = ["name", "email"]
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def __str__(self):
        return self.name or self.email

    @property
    def first_name(self):
        return (self.name or self.email.split("@")[0]).split()[0]

    @property
    def initials(self):
        parts = (self.name or self.email).replace("@", " ").split()
        return "".join(p[0] for p in parts[:2]).upper()

    @property
    def role_name(self):
        if self.is_superuser:
            return "Superusuario"
        return self.role.name if self.role else "Sin rol"

    # --- permisos -------------------------------------------------------
    def _flag(self, name):
        return self.is_superuser or bool(self.role and getattr(self.role, name))

    @property
    def sees_all(self):
        return self.is_superuser or bool(self.role and self.role.view_scope == Role.Scope.ALL)

    @property
    def leads_team(self):
        """CEO, Director y Responsable Comercial: siguen la actividad de todo el equipo, cambios de leads incluidos."""
        return self.is_superuser or bool(self.role and self.role.code in ("ceo", "director", "responsable-comercial"))

    @property
    def is_management(self):
        """CEO y Director: además ven las últimas empresas y contactos dados de alta."""
        return self.is_superuser or bool(self.role and self.role.code in ("ceo", "director"))

    @property
    def edits_all(self):
        return self.is_superuser or bool(self.role and self.role.edit_scope == Role.Scope.ALL)

    @property
    def can_delete(self):
        return self._flag("can_delete")

    @property
    def can_export(self):
        return self._flag("can_export")

    @property
    def can_manage_catalog(self):
        return self._flag("can_manage_catalog")

    @property
    def can_manage_pipeline(self):
        return self._flag("can_manage_pipeline")

    @property
    def can_manage_knowledge(self):
        return self._flag("can_manage_knowledge")

    @property
    def can_manage_incentives(self):
        return self._flag("can_manage_incentives")

    @property
    def can_manage_events(self):
        return self._flag("can_manage_events")

    @property
    def can_use_api(self):
        return self._flag("can_use_api")

    @property
    def can_see_reports(self):
        return self.is_superuser

    def can_edit(self, obj):
        """Propietario por registro: edita quien lo tiene asignado o quien edita todo."""
        if self.edits_all:
            return True
        owners = {getattr(obj, "owner_id", None), getattr(obj, "assignee_id", None), getattr(obj, "user_id", None)}
        if self.pk in owners:
            return True
        extra = getattr(obj, "assignees", None)
        if extra is not None and obj.pk and extra.filter(pk=self.pk).exists():
            return True
        # sin propietario propio (línea, suscripción, bolsa, presupuesto): manda el registro del que cuelga
        if not any(hasattr(obj, name) for name in ("owner_id", "assignee_id", "user_id")):
            parent = getattr(obj, "opportunity", None) or getattr(obj, "company", None)
            return parent is not None and self.can_edit(parent)
        return False


def _hash_code(code):
    return hmac.new(settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256).hexdigest()


class LoginCode(models.Model):
    """Código de un solo uso enviado al correo. Solo se guarda su hash."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="login_codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def issue(cls, user):
        """Devuelve un código nuevo, o None si ya se le envió uno hace poco: como mucho un correo cada 10 minutos por persona.

        El código enviado sigue valiendo mientras no caduque, también en un segundo dispositivo, así que no hace falta otro.
        """
        recent = timezone.now() - timedelta(seconds=settings.LOGIN_CODE_RESEND_SECONDS)
        if cls.objects.filter(user=user, created_at__gte=recent).exists():
            return None
        window = timezone.now() - timedelta(minutes=settings.LOGIN_CODE_WINDOW_MINUTES)
        if cls.objects.filter(user=user, created_at__gte=window).count() >= settings.LOGIN_CODE_MAX_PER_WINDOW:
            return None
        code = f"{secrets.randbelow(10**6):06d}"
        cls.objects.filter(user=user, used_at__isnull=True).update(used_at=timezone.now())
        cls.objects.create(
            user=user,
            code_hash=_hash_code(code),
            expires_at=timezone.now() + timedelta(minutes=settings.LOGIN_CODE_TTL_MINUTES),
        )
        return code

    @classmethod
    def verify(cls, user, code):
        entry = cls.objects.filter(user=user, used_at__isnull=True, expires_at__gt=timezone.now()).first()
        if not entry or entry.attempts >= settings.LOGIN_CODE_MAX_ATTEMPTS:
            return False
        ok = hmac.compare_digest(entry.code_hash, _hash_code(code.strip()))
        # un acierto no lo gasta: vale durante sus 10 minutos para entrar también desde otro dispositivo sin otro correo
        if not ok:
            cls.objects.filter(pk=entry.pk).update(attempts=models.F("attempts") + 1)
        return ok


class AccessLog(models.Model):
    """Registro de accesos (RGPD): quién consultó, cambió o exportó qué."""

    class Action(models.TextChoices):
        LOGIN = "login", "Inicio de sesión"
        VIEW = "view", "Consulta"
        CREATE = "create", "Alta"
        UPDATE = "update", "Cambio"
        DELETE = "delete", "Borrado"
        EXPORT = "export", "Exportación"
        ERASE = "erase", "Supresión RGPD"

    user = models.ForeignKey(User, null=True, on_delete=models.SET_NULL, related_name="access_logs")
    action = models.CharField(max_length=10, choices=Action.choices)
    object_type = models.CharField(max_length=40, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    object_repr = models.CharField(max_length=200, blank=True)
    channel = models.CharField(max_length=10, default="web")  # web / api / mcp
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["object_type", "object_id"])]
        verbose_name = "registro de acceso"
        verbose_name_plural = "registro de accesos"


def log_access(request, action, obj=None, channel=None, label=""):
    user = getattr(request, "user", None)
    ip = None
    if request is not None:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get("REMOTE_ADDR")) or None
    AccessLog.objects.create(
        user=user if user and user.is_authenticated else None,
        action=action,
        object_type=obj._meta.model_name if obj is not None else label,
        object_id=str(obj.pk) if obj is not None and obj.pk else "",
        object_repr=str(obj)[:200] if obj is not None else "",
        channel=channel or ("api" if request is not None and request.path.startswith("/api/") else "web"),
        ip=ip,
    )
