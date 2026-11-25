# Configuración

Toda la configuración vive en el fichero `.env` de la raíz, que el instalador crea a partir de
[`.env.example`](../.env.example). Después de cambiarlo: `./qopencrm.sh restart`.

`.env` contiene secretos: tiene permisos `600` y nunca se sube al repositorio.

## Esenciales

| Variable | Por defecto | Para qué |
|---|---|---|
| `DJANGO_SECRET_KEY` | la genera el instalador | Firma sesiones y cifra tokens de integraciones. No la cambies una vez en uso. |
| `DJANGO_DEBUG` | `0` en Docker | `1` solo en desarrollo. |
| `SITE_URL` | `http://localhost:8000` | Dirección pública. Si empieza por `https://` se activan cookies seguras, HSTS y el proxy Caddy. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Nombres de host admitidos, separados por comas. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | vacío | Orígenes adicionales de confianza, si accedes por más de una dirección. |
| `WEB_PORT` | `8000` | Puerto local de la web (solo en `127.0.0.1`). |
| `PROXY` | `1` | `0` si ya tienes un proxy inverso con TLS delante y no quieres Caddy. |

## Base de datos

| Variable | Por defecto | Para qué |
|---|---|---|
| `POSTGRES_DB`, `POSTGRES_USER` | `qopencrm` | Nombre de la base de datos y usuario. |
| `POSTGRES_PASSWORD` | la genera el instalador | Contraseña. |
| `POSTGRES_HOST`, `POSTGRES_PORT` | `127.0.0.1`, `5433` | Solo para ejecutar Django fuera de Docker. Dentro, los contenedores usan `db:5432`. |
| `DB_HOST_PORT` | `5433` | Puerto de PostgreSQL publicado en el anfitrión (solo en `127.0.0.1`). |

## Correo saliente

Lo usan los códigos de acceso, los recordatorios de tareas, el resumen diario y el envío de presupuestos.

```ini
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.tu-proveedor.com
EMAIL_PORT=587
EMAIL_HOST_USER=crm@tu-dominio.com
EMAIL_HOST_PASSWORD=...
DEFAULT_FROM_EMAIL=QopenCRM <crm@tu-dominio.com>
```

Con el puerto `465` se usa SSL; con cualquier otro, STARTTLS (`EMAIL_USE_TLS=0` lo desactiva). Con el valor por
defecto de `EMAIL_BACKEND` (consola) nada se envía: los correos salen en `./qopencrm.sh logs web`.

## Captura automática de correo y reuniones

Un buzón compartido, por ejemplo `ventas@tu-dominio.com`, que el equipo pone en copia (CC o CCO) o al que reenvía.
El `worker` lo lee cada 5 minutos por IMAP **en modo solo lectura** y anota en el historial de la empresa cada mensaje
que cruza con un contacto: correos entrantes y salientes, reenvíos e invitaciones de calendario (como reunión). Las
personas nuevas de un dominio ya conocido se dan de alta solas; los dominios genéricos (gmail.com y similares) no.

| Variable | Por defecto | Para qué |
|---|---|---|
| `MAIL_CAPTURE_HOST` | vacío (desactivado) | Servidor IMAP. |
| `MAIL_CAPTURE_PORT` | `993` | Puerto IMAP con SSL. |
| `MAIL_CAPTURE_USER`, `MAIL_CAPTURE_PASSWORD` | vacío | Credenciales del buzón. |
| `MAIL_CAPTURE_FOLDERS` | `INBOX` | Carpetas, separadas por comas. |
| `MAIL_CAPTURE_SINCE_DAYS` | `30` | La primera lectura solo mira los últimos N días. |

Después de importar contactos, para volver a cruzar los mensajes ya leídos:

```bash
./qopencrm.sh manage sync_mail --rescan
```

## Inteligencia artificial

| Variable | Por defecto | Para qué |
|---|---|---|
| `AI_PROVIDER` | `openai` | `openai` o `anthropic`. |
| `AI_API_KEY` | vacío (desactivado) | Clave del proveedor. Sin ella, las funciones de IA no aparecen. |
| `AI_MODEL` | el del proveedor | Modelo concreto, si quieres uno distinto del predeterminado. |

Estas variables activan «Rellenar con IA» y la búsqueda en lenguaje natural. El **servidor MCP no las necesita**: lo
usa el asistente de cada persona con su propia cuenta. Ver [IA.md](IA.md).

## Presupuestos e identidad

| Qué | Dónde |
|---|---|
| Razón social, dirección y pie (IBAN, CIF) | En el CRM: **Ajustes → Plantillas de presupuesto**. |
| Logo de los PDF | Fichero `branding/logo.png` (PNG con fondo transparente, unos 400 px de ancho). |
| Prefijo de la numeración | `QUOTE_NUMBER_PREFIX` (por defecto `P`: `P26 000001`, `P26 000002`…). Le sigue el año en dos cifras. |
| Colores de la interfaz | Variables CSS al principio de `static/css/app.css`. |
| Etapas, probabilidades, motivos de pérdida y roles | En el CRM: **Ajustes**. |

## Copias de seguridad

| Variable | Por defecto | Para qué |
|---|---|---|
| `BACKUP_KEEP` | `14` | Copias locales que se conservan. |
| `FTP_PROTOCOL` | `ftps` | `ftp`, `ftps` o `sftp`. Evita `ftp`: viaja sin cifrar. |
| `FTP_HOST`, `FTP_PORT`, `FTP_USER`, `FTP_PASSWORD` | vacío | Servidor remoto para `backup-ftp`. |
| `FTP_DIR` | `/qopencrm` | Carpeta remota. |
| `FTP_KEEP` | `30` | Copias que se conservan en el remoto (`0` = no borrar nunca). |

Procedimiento completo en [DESPLIEGUE.md](DESPLIEGUE.md#copias-de-seguridad).

## Microsoft Teams (opcional)

Se configura dentro del CRM, no en `.env`: **Ajustes → Reuniones de Teams** (solo superusuario). Necesitas registrar una
aplicación en Microsoft Entra ID con los permisos `Calendars.ReadWrite` y `OnlineMeetings.ReadWrite` y, como URI de
redirección, `https://tu-crm/ajustes/microsoft/callback/`. Cada persona conecta después su cuenta desde Ajustes. El CRM
nunca ve contraseñas: guarda cifrado el token de renovación.

## App Android (opcional)

| Variable | Por defecto | Para qué |
|---|---|---|
| `ANDROID_PACKAGE` | `org.qopencrm.app` | Identificador de la app que compiles. |
| `ANDROID_SHA256` | vacío | Huella del certificado con el que firmas el APK; la imprime `android/build.sh`. |

Ver [DESPLIEGUE.md](DESPLIEGUE.md#app-android).
