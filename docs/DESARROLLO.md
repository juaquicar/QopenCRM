# Desarrollo

Guía para quien quiera modificar QopenCRM o contribuir. Antes de enviar cambios, lee también
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Entorno local

Necesitas Python 3.14, [uv](https://docs.astral.sh/uv/) y Docker (solo para PostgreSQL). Para generar los PDF hacen
falta las bibliotecas de Pango: en Debian o Ubuntu, `sudo apt install libpango-1.0-0 libpangoft2-1.0-0`; en macOS,
`brew install pango`.

```bash
git clone https://github.com/juaquicar/QopenCRM.git
cd QopenCRM
cp .env.example .env                 # los valores por defecto sirven para desarrollo
docker compose up -d db              # PostgreSQL en 127.0.0.1:5433
uv sync                              # crea .venv con las dependencias
uv run python manage.py migrate
uv run python manage.py seed --admin tu@correo.example
uv run python manage.py seed_demo    # opcional: datos ficticios
uv run python manage.py runserver
```

Abre `http://localhost:8000`. Con `DJANGO_DEBUG=1` el código de acceso sale en la consola de `runserver`.

El `worker` (recordatorios, webhooks, captura de correo) es otro proceso; solo hace falta si trabajas en esas partes:

```bash
uv run python manage.py runworker --interval 30
```

## Pruebas

```bash
uv run python manage.py test apps                    # todo
uv run python manage.py test apps.crm                # una aplicación
uv run python manage.py test apps.crm.tests.ApkTests # una clase
uv run python manage.py makemigrations --check       # no debe faltar ninguna migración
```

Las pruebas usan PostgreSQL (el mismo contenedor `db`) y no salen a la red: correo, proveedores de IA y Microsoft
Graph se sustituyen por dobles. La integración continua ejecuta exactamente estos dos comandos en cada PR.

## Arquitectura

Un monolito Django con renderizado en servidor. La interactividad (diálogos, tablero arrastrable, filtros) es htmx
más un único fichero de JavaScript sin compilar: no hay paso de construcción del frontend.

```
config/                 settings.py (todo sale de .env), urls.py
apps/
  accounts/             User (correo como identificador), Role, LoginCode, AccessLog, permisos
  catalog/              ProductFamily, Product, BillingModel
  crm/                  Company, Contact, Stage, Opportunity, OpportunityLine, Activity, Task, Event
                        vistas, formularios, filtros, informes, importación y exportación
  sales/                Quote y QuoteTemplate (PDF), Subscription, HourBank; services.py con la lógica
  integrations/         api.py (DRF), mcp.py, oauth.py, webhooks.py, mail.py, ai.py, microsoft.py
  incentives/           Incentive y programa de productividad
  knowledge/            Article (Markdown)
templates/              plantillas Django; _icons.html es el sprite SVG
static/css/app.css      una sola hoja de estilos; el tema son las variables del principio
static/js/app.js        diálogos, arrastrar y soltar, atajos
docker/                 entrypoint, Caddyfile y fuentes de los PDF
android/                proyecto de la app Android (Trusted Web Activity)
qopencrm.sh             instalación, operación y copias
```

### Ideas que conviene conocer

- **Permisos por rol y por registro.** `Role` define el alcance (`own` o `all`) para ver y para editar, más permisos
  sueltos (exportar, borrar, gestionar catálogo…). `apps.accounts.permissions.scope(queryset, user)` filtra lo que una
  persona puede ver y `user.can_edit(obj)` decide si puede cambiarlo. **Toda vista, punto de la API o herramienta MCP
  nueva tiene que pasar por ahí.**
- **Sesión obligatoria por defecto.** `LoginRequiredMiddleware` protege todas las vistas; las públicas se marcan con
  `login_not_required`.
- **Tres tipos de importe, nunca sumados.** Recurrente mensual, servicios por horas y pago único
  (`catalog.BillingModel`). `Opportunity.recalc()` los recalcula a partir de las líneas.
- **Próxima acción.** Es la tarea pendiente más cercana de la oportunidad; `refresh_next_action()` mantiene la fecha
  desnormalizada que usan los filtros.
- **Los presupuestos no se modifican.** `sales.services.save_quote` crea siempre una versión nueva con su PDF.
- **Ganar una oportunidad** (`sales.services.fulfil_won`) convierte sus líneas en suscripciones y bolsas de horas.
- **Registro de accesos.** `log_access(request, acción, objeto)` deja rastro de consultas, cambios, exportaciones y
  supresiones, con su canal (`web`, `api` o `mcp`).
- **Webhooks.** `integrations.webhooks.emit("evento", datos)` encola la entrega tras confirmar la transacción.

### Diálogos

Cualquier elemento con `data-modal="/url"` abre esa URL en el diálogo. Los formularios de alta y edición comparten una
vista genérica: basta registrar el formulario en `MODALS` (`apps/crm/views.py`) con su comprobación de permisos.

## Tareas habituales

### Añadir una herramienta MCP

En `apps/integrations/mcp.py`:

```python
@tool("count_companies", "Cuántas empresas ve la persona conectada.", {"kind": S})
def count_companies(user, args):
    queryset = scope(Company.objects.all(), user)          # siempre el alcance de la persona
    if args.get("kind"):
        queryset = queryset.filter(kind=args["kind"])
    return {"count": queryset.count()}
```

- La descripción es lo que lee el asistente: di qué hace y cuándo usarla.
- `write=True` si modifica datos y `destructive=True` si borra o escribe a un cliente.
- `needs="catalog"` (u otro permiso) si exige un permiso de rol.
- Lanza `ToolError("mensaje")` para fallos que el asistente puede corregir.
- Añade una prueba en `apps/integrations/tests.py` y la fila correspondiente en [IA.md](IA.md).

### Añadir un campo a un modelo

1. Campo en el modelo, con `verbose_name` en español.
2. `uv run python manage.py makemigrations`.
3. Formulario (`forms.py`), plantilla y, si procede, serializador (`integrations/api.py`) y herramienta MCP.
4. Prueba.

### Añadir un evento de webhook

Añádelo a `Webhook.EVENTS` (`apps/integrations/models.py`), llama a `emit(...)` donde ocurra y documéntalo en
[API.md](API.md).

### Cambiar el aspecto

El tema son las variables CSS del principio de `static/css/app.css` (oscuro por defecto, claro bajo
`:root[data-theme="light"]`). Los iconos son símbolos SVG en `templates/_icons.html`.

### Rehacer las capturas del manual

El manual de usuario (`templates/crm/manual.html`) usa las imágenes de `static/img/manual/`. Se hacen sobre los datos
de `seed_demo`, a 1280 × 800 y escala 1,5, y se guardan en WebP.

## Convenciones

- **Idioma.** Interfaz, comentarios y documentación en español; identificadores de código en inglés.
- **Estilo.** Sigue el del fichero que tocas: funciones cortas, líneas largas admitidas si se leen bien y comentarios solo donde expliquen un porqué.
- **Dependencias.** Las menos posibles. Nada que exija compilar el frontend.
- **Migraciones.** Una por cambio de modelo, generada con `makemigrations` y revisada a mano.
- **Seguridad.** Ningún secreto en el repositorio ni en la base de datos sin cifrar; toda consulta, acotada con
  `scope()`.

## Versiones

Versionado semántico. Cada cambio visible se anota en [CHANGELOG.md](../CHANGELOG.md) bajo «Sin publicar».
