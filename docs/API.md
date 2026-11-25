# API REST y webhooks

QopenCRM expone todo lo que hace la web por una API REST, y avisa a otros sistemas con webhooks firmados.

- **Base:** `https://tu-crm/api/v1/`
- **Documentación interactiva (Swagger):** `https://tu-crm/api/docs/`
- **Esquema OpenAPI:** `https://tu-crm/api/schema/`

## Autenticación

Cada persona crea sus claves en **Ajustes → Mis claves de API**. La clave se muestra una sola vez; en el servidor solo
se guarda su resumen (hash). Se envía en la cabecera `Authorization`:

```bash
curl -H "Authorization: Bearer qcrm_TU_CLAVE" https://tu-crm/api/v1/me/
```

Una clave actúa **con los permisos de su dueño**: ve y edita lo mismo que esa persona en la web. Se puede revocar en
cualquier momento, y cada uso queda en el registro de accesos con el canal `api`. El rol necesita el permiso «puede
usar API y MCP».

## Recursos

| Ruta | Recurso |
|---|---|
| `/me/` | La persona dueña de la clave, su rol y sus permisos. |
| `/companies/` | Empresas (clientes y posibles clientes). |
| `/contacts/` | Contactos. |
| `/opportunities/` | Oportunidades. |
| `/opportunity-lines/` | Líneas de producto de una oportunidad. |
| `/activities/` | Historial: notas, llamadas, correos, reuniones y cambios de etapa. |
| `/tasks/` | Tareas y recordatorios. |
| `/products/`, `/product-families/` | Catálogo. |
| `/stages/`, `/loss-reasons/` | Etapas del pipeline y motivos de pérdida. |
| `/subscriptions/` | Suscripciones (recurrente en cartera). |
| `/hour-banks/`, `/hour-entries/` | Bolsas de horas y sus consumos. |
| `/quotes/` | Presupuestos. |
| `/users/` | Equipo. |
| `/reports/` | Informe de dirección. |
| `/export/` | Exportación completa en un ZIP de CSV (requiere permiso de exportación). |

Los listados admiten paginación (`?limit=50&offset=0`), búsqueda (`?search=`), orden (`?ordering=`) y los filtros de
cada recurso, que están descritos en `/api/docs/`.

## Ejemplos

Crear una empresa:

```bash
curl -X POST https://tu-crm/api/v1/companies/ \
  -H "Authorization: Bearer qcrm_TU_CLAVE" -H "Content-Type: application/json" \
  -d '{"name": "Operadora del Sur", "website": "www.operadoradelsur.example", "city": "Sevilla"}'
```

Oportunidades abiertas sin próxima acción:

```bash
curl -H "Authorization: Bearer qcrm_TU_CLAVE" "https://tu-crm/api/v1/opportunities/?status=open&action=none"
```

En Python:

```python
import requests

api = requests.Session()
api.headers["Authorization"] = "Bearer qcrm_TU_CLAVE"
for opportunity in api.get("https://tu-crm/api/v1/opportunities/", params={"status": "open"}).json()["results"]:
    print(opportunity["title"], opportunity["amount_mrr"])
```

Los nombres exactos de campos y filtros están siempre al día en `/api/docs/`: se generan a partir del código.

## Webhooks

El superusuario los da de alta en **Ajustes → Webhooks**: una URL, un secreto de firma y los eventos que interesan
(ninguno marcado = todos).

| Evento | Cuándo |
|---|---|
| `company.created`, `company.updated` | Alta o cambio de una empresa. |
| `contact.created` | Alta de un contacto. |
| `opportunity.created` | Alta de una oportunidad. |
| `opportunity.stage_changed` | Cambio de etapa. |
| `opportunity.won`, `opportunity.lost` | Oportunidad ganada o perdida. |
| `quote.created` | Presupuesto generado (cada versión). |
| `subscription.changed` | Alta, cambio o baja de una suscripción. |

Cada entrega es un `POST` con este cuerpo:

```json
{"event": "opportunity.won", "data": {"id": 42}, "sent_at": "2026-01-15T10:30:00+01:00"}
```

y dos cabeceras: `X-QopenCRM-Event` y `X-QopenCRM-Signature: sha256=<hmac>`.

### Verificar la firma

La firma es el HMAC-SHA256 del cuerpo, tal cual llega, con el secreto del webhook. Compárala siempre en tiempo
constante:

```python
import hashlib
import hmac


def is_valid(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header)
```

### Reintentos

Responde con un código 2xx en menos de 10 segundos. Si la entrega falla, el `worker` la reintenta cada 5 minutos,
hasta 6 intentos y durante 48 horas como máximo. Las entregas pueden repetirse: haz que tu receptor sea idempotente.

## Otras vías de integración

- **Servidor MCP**, para asistentes de IA: [IA.md](IA.md).
- **Importación CSV** de empresas y contactos, con deduplicación: **Ajustes → Datos → Importar**.
- **Banco de conocimiento** desde una carpeta de Markdown: `./qopencrm.sh manage import_knowledge <carpeta>`.
