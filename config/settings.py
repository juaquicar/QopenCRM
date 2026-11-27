"""Configuración de QopenCRM."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(name, default=""):
    return os.environ.get(name, default)


SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG", "0") == "1"
SITE_URL = env("SITE_URL", "http://localhost:8000").rstrip("/")
ALLOWED_HOSTS = [h.strip() for h in env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [SITE_URL] + [o.strip() for o in env("DJANGO_CSRF_TRUSTED_ORIGINS").split(",") if o.strip()]
# detrás de un proxy inverso (Nginx, Caddy, Traefik) que termina el TLS
HTTPS = SITE_URL.startswith("https://")
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "rest_framework",
    "django_filters",
    "drf_spectacular",
    "apps.accounts",
    "apps.catalog",
    "apps.crm",
    "apps.sales",
    "apps.integrations",
    "apps.knowledge",
    "apps.incentives",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.crm.context.shell",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "qopencrm"),
        "USER": env("POSTGRES_USER", "qopencrm"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "127.0.0.1"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS

# Acceso por código enviado al correo
LOGIN_CODE_TTL_MINUTES = 10
LOGIN_CODE_MAX_ATTEMPTS = 5
LOGIN_CODE_RESEND_SECONDS = 600  # como mucho un correo con código cada 10 minutos por persona
# como mucho 5 códigos por usuario cada 15 minutos: con 5 intentos por código, adivinarlo no es viable
LOGIN_CODE_MAX_PER_WINDOW = 5
LOGIN_CODE_WINDOW_MINUTES = 60

LANGUAGE_CODE = "es"
TIME_ZONE = "Europe/Madrid"
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        if DEBUG
        else "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

EMAIL_BACKEND = env("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = env("EMAIL_HOST")
EMAIL_PORT = int(env("EMAIL_PORT", "587") or 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD")
EMAIL_USE_SSL = EMAIL_PORT == 465
EMAIL_USE_TLS = not EMAIL_USE_SSL and env("EMAIL_USE_TLS", "1") == "1"
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "QopenCRM <crm@example.com>")
# prefijo del asunto en los correos internos (códigos, recordatorios, menciones); no en los que van a clientes
INTERNAL_EMAIL_PREFIX = env("INTERNAL_EMAIL_PREFIX", "[QopenCRM]")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.integrations.auth.ApiKeyAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["apps.accounts.permissions.CrmPermission"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}
SPECTACULAR_SETTINGS = {
    "TITLE": "QopenCRM API",
    "DESCRIPTION": "API REST de QopenCRM, el CRM comercial de código abierto con IA.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# Captura automática de correo: buzón compartido que el equipo pone en copia
MAIL_CAPTURE_HOST = env("MAIL_CAPTURE_HOST")
MAIL_CAPTURE_PORT = int(env("MAIL_CAPTURE_PORT", "993") or 993)
MAIL_CAPTURE_USER = env("MAIL_CAPTURE_USER")
MAIL_CAPTURE_PASSWORD = env("MAIL_CAPTURE_PASSWORD")
MAIL_CAPTURE_FOLDERS = [f.strip() for f in env("MAIL_CAPTURE_FOLDERS", "INBOX").split(",") if f.strip()]
MAIL_CAPTURE_SINCE_DAYS = int(env("MAIL_CAPTURE_SINCE_DAYS", "30") or 30)

# Asistente de IA (opcional): la clave se configura por entorno, nunca en la base de datos
AI_PROVIDER = env("AI_PROVIDER", "openai").lower()  # openai o anthropic
AI_API_KEY = env("AI_API_KEY")
AI_MODEL = env("AI_MODEL")  # vacío = modelo por defecto del proveedor

# App Android (TWA): huella del certificado con el que se firma el APK, para abrirla sin barra del navegador
ANDROID_PACKAGE = env("ANDROID_PACKAGE", "org.qopencrm.app")
ANDROID_SHA256 = [f.strip() for f in env("ANDROID_SHA256").split(",") if f.strip()]

# Identidad de la instalación: branding/logo.png sustituye al logo genérico de los presupuestos en PDF
BRANDING_DIR = Path(env("BRANDING_DIR", str(BASE_DIR / "branding")))
# Presupuestos: prefijo de la numeración (P26 000001, P26 000002…). Le sigue el año en dos cifras.
QUOTE_NUMBER_PREFIX = env("QUOTE_NUMBER_PREFIX", "P")
# NDA: datos de tu empresa como parte firmante del acuerdo de confidencialidad (se rige por la legislación española).
# Dirección, cargo y ciudad de los tribunales admiten versión en inglés (…_EN); si falta, se usa la española.
NDA_ISSUER = {
    "name": env("NDA_ISSUER_NAME", "TU EMPRESA, S.L."),
    "tax_id": env("NDA_ISSUER_TAX_ID", "B00000000"),
    "signer": env("NDA_ISSUER_SIGNER", "Nombre Apellido"),
    "address": {"es": env("NDA_ISSUER_ADDRESS", "Calle Ejemplo, 1, 00000 Ciudad")},
    "position": {"es": env("NDA_ISSUER_POSITION", "Administrador único")},
    "courts": {"es": env("NDA_ISSUER_COURTS", "Madrid")},
}
for _key, _default in (("address", ""), ("position", "Sole Director"), ("courts", "")):
    NDA_ISSUER[_key]["en"] = env(f"NDA_ISSUER_{_key.upper()}_EN", "" if env(f"NDA_ISSUER_{_key.upper()}") else _default) or NDA_ISSUER[_key]["es"]

# Precios del catálogo desde Stripe (manage.py sync_stripe). Mejor una clave restringida de solo lectura (rk_live_…)
STRIPE_SECRET_KEY = env("STRIPE_SECRET_KEY")
