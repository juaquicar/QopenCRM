# Inteligencia artificial en QopenCRM

QopenCRM usa la IA de dos maneras, independientes entre sí:

1. **Servidor MCP:** tu asistente (Claude u otro cliente compatible con el
   [Model Context Protocol](https://modelcontextprotocol.io)) usa el CRM como un conjunto de herramientas. No
   necesita ninguna clave de proveedor en el servidor: cada persona lo conecta con su propio asistente.
2. **Funciones integradas:** «Rellenar con IA» y la búsqueda en lenguaje natural del pipeline, que sí usan una clave
   de OpenAI o Anthropic configurada en `.env`.

## 1. Servidor MCP

Cada instalación publica el servidor en `{SITE_URL}/mcp/`. La dirección exacta y las herramientas que tiene tu rol
están en **Ajustes → Asistente de IA (MCP)**.

### Conectar Claude (web, escritorio o móvil)

1. En Claude: Ajustes → Conectores → **Añadir conector personalizado**.
2. Pega la dirección `https://tu-crm/mcp/`. No hace falta ninguna clave.
3. Se abre el CRM: entra con tu correo y pulsa **Autorizar**.

Por debajo es OAuth 2.1 con registro dinámico de clientes y PKCE (S256 obligatorio). El token de acceso dura una hora
y el de renovación, que rota en cada uso, 30 días. Puedes retirar el acceso a cualquier aplicación desde Ajustes.

El CRM tiene que ser accesible por HTTPS desde internet para que un conector web llegue a él.

### Conectar con una clave de API (Claude Code y otros)

Crea una clave en **Ajustes → Mis claves de API** (solo se muestra una vez).

```bash
claude mcp add --transport http qopencrm https://tu-crm/mcp/ --header "Authorization: Bearer qcrm_TU_CLAVE"
```

Cualquier otro cliente MCP con transporte HTTP:

```json
{"mcpServers": {"qopencrm": {"type": "http", "url": "https://tu-crm/mcp/",
  "headers": {"Authorization": "Bearer qcrm_TU_CLAVE"}}}}
```

### Qué puedes pedirle

- «¿Qué tengo para hoy?»
- «Enséñame las oportunidades sin próxima acción.»
- «Crea un lead de Operadora del Sur con Plan Plus y recuérdame llamar el jueves.»
- «Anota que he hablado con Marta de Fibranet y quiere una demo la semana que viene.»
- «Mueve Cable Atlántico a Negociación y genera el presupuesto.»
- «¿Cuánto recurrente hay en juego en Propuesta enviada?»

### Permisos y seguridad

- **El asistente es la persona conectada.** Ve lo que ella ve y solo cambia lo que ella puede cambiar: el alcance por
  rol (lo suyo o todo) se aplica registro a registro, igual que en la web.
- **Herramientas por rol.** El superusuario puede recortar, rol a rol, qué herramientas se ofrecen. Un rol sin
  permiso de API no puede usar el MCP.
- **Acciones delicadas marcadas.** Las herramientas que borran o que escriben a un cliente van anotadas como
  destructivas para que el asistente pida confirmación. `send_quote` es lo único con lo que el CRM escribe a un
  cliente.
- **Todo queda registrado.** Cada lectura y cada cambio hecho por MCP aparece en el registro de accesos con el canal
  `mcp`.
- **Sin estado.** Transporte HTTP con JSON-RPC 2.0 en un único punto de entrada (`POST /mcp/`), sin SSE. Versiones
  del protocolo admitidas: 2025-06-18, 2025-03-26 y 2024-11-05.

### Herramientas (43)

| Herramienta | Tipo | Qué hace |
|---|---|---|
| `whoami` | consulta | Quién está conectado, su rol y qué puede hacer. |
| `list_team` | consulta | Personas del equipo comercial, con su id, correo y rol. |
| `list_stages` | consulta | Etapas del pipeline en orden, con su probabilidad, y motivos de pérdida disponibles. |
| `list_events` | consulta | Ferias y eventos por fecha (los más recientes primero), con cuántas empresas de las que ves se conocieron en cada uno. |
| `create_event` | modifica | Da de alta una feria o evento para poder asignarlo a las empresas conocidas allí. |
| `search_companies` | consulta | Busca empresas (clientes y posibles clientes) por nombre, CIF o dominio. |
| `get_company` | consulta | Ficha de una empresa: datos, contactos, oportunidades, tareas pendientes, suscripciones y última actividad. |
| `search_contacts` | consulta | Busca personas por nombre, correo, cargo o empresa. |
| `list_opportunities` | consulta | Oportunidades (leads) con filtros. |
| `get_opportunity` | consulta | Detalle de una oportunidad: líneas de producto, próxima acción, tareas, presupuestos e historial. |
| `pipeline_summary` | consulta | Resumen del pipeline visible para ti: por etapa, número de oportunidades y lo que hay en juego (MRR, horas y pago único por separado), más cuántas no tienen próxima acción o la tienen vencida. |
| `list_tasks` | consulta | Tareas y recordatorios. |
| `list_activity` | consulta | Historial (notas, llamadas, correos, reuniones, cambios de etapa) de una empresa o de una oportunidad. |
| `list_products` | consulta | Catálogo de productos con su modelo de cobro (monthly, hourly, one_off) y precio. |
| `list_quotes` | consulta | Presupuestos (última versión de cada número) con su importe y estado. |
| `get_quote` | consulta | Un presupuesto con sus líneas, notas y versiones. |
| `list_subscriptions` | consulta | Suscripciones (recurrente en cartera) y bolsas de horas de los clientes que ves. |
| `my_incentives` | consulta | Incentivos de la persona conectada (ventas, cuota recurrente y PM): previsto, devengado según lo cobrado, abonado y pendiente de validar. |
| `get_report` | consulta | Informe de dirección: valor del pipeline por etapa, conversión, motivos de pérdida, recurrente y previsión. |
| `create_company` | modifica | Da de alta una empresa. |
| `update_company` | modifica | Cambia datos de una empresa. |
| `create_contact` | modifica | Da de alta una persona en una empresa. |
| `update_contact` | modifica | Cambia datos de un contacto. |
| `erase_contact` | confirma antes | RGPD, derecho de supresión: borra los datos personales de un contacto. |
| `create_opportunity` | modifica | Crea una oportunidad (lead). |
| `update_opportunity` | modifica | Cambia datos de una oportunidad (no la etapa: para eso, `move_opportunity`). |
| `move_opportunity` | modifica | Cambia una oportunidad de etapa. |
| `hold_opportunity` | modifica | Para una oportunidad a la espera del cliente, con fecha de revisión obligatoria (máximo 90 días). |
| `resume_opportunity` | modifica | Reanuda una oportunidad parada. |
| `add_line` | modifica | Añade una línea a una oportunidad: un producto del catálogo (product = su código) o una línea libre con su billing_model (monthly = recurrente, hourly = por horas, one_off = pago único), descripción y precio. |
| `remove_line` | confirma antes | Quita una línea de una oportunidad. |
| `log_activity` | modifica | Anota en el historial algo que ha pasado con un cliente: nota, llamada, correo o reunión. |
| `create_task` | modifica | Crea una tarea o recordatorio. |
| `complete_task` | modifica | Marca una tarea como hecha. |
| `reschedule_task` | modifica | Cambia la fecha, el texto o la persona de una tarea. |
| `create_quote` | modifica | Genera un presupuesto en PDF para una oportunidad. |
| `send_quote` | confirma antes | ENVÍA un presupuesto por correo al cliente, con el PDF adjunto. |
| `set_quote_status` | modifica | Marca un presupuesto como borrador, enviado, aceptado o rechazado. |
| `log_hours` | modifica | Apunta horas consumidas en una bolsa de horas de un cliente. |
| `change_subscription` | modifica | Cambia cantidad, precio o plan de una suscripción. |
| `cancel_subscription` | confirma antes | Da de baja una suscripción: deja de contar en el recurrente. |
| `create_product` | modifica | Añade un producto al catálogo. |
| `update_product` | modifica | Cambia un producto del catálogo (precio, estado…). |

Para añadir una herramienta nueva, ver [DESARROLLO.md](DESARROLLO.md#añadir-una-herramienta-mcp).

## 2. Funciones integradas

Se activan con una clave de proveedor en `.env`:

```ini
AI_PROVIDER=anthropic        # o openai
AI_API_KEY=...
AI_MODEL=                    # vacío = modelo por defecto del proveedor
```

| Función | Dónde | Qué hace |
|---|---|---|
| **Rellenar con IA** | Formularios de empresa y de contacto | Pegas una firma de correo, una tarjeta o un párrafo de una web y rellena los campos. Solo usa lo que aparece en el texto. |
| **Buscar con IA** | Pipeline | Escribes «lo de Plan Pro sin próxima acción de Ana» o «propuestas que cierran este mes» y se aplican los filtros. |

Sin `AI_API_KEY` estas funciones no aparecen y el resto del CRM funciona igual.

## Privacidad

- **Funciones integradas:** al proveedor solo se envía el texto que la persona pega o escribe y, para la búsqueda, los
  nombres de las etapas, productos, familias y personas del equipo, que hacen falta para traducir la frase a filtros.
  Nunca se envía la base de datos.
- **MCP:** el CRM no llama a ningún proveedor de IA. Es el asistente de cada persona el que pide datos al CRM, con sus
  permisos; lo que ese asistente haga con ellos depende de las condiciones del proveedor que cada cual use.
- La clave de proveedor se configura por entorno y nunca se guarda en la base de datos.
