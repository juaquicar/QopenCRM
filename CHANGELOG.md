# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
[versionado semántico](https://semver.org/lang/es/).

## Sin publicar

### Añadido

- Menciones con `@` en notas y tareas: avisan en el CRM (sección **Menciones**) y por correo; `@todos` avisa a
  todas las personas implicadas en un lead.
- Contactos en varias empresas y con varios correos y teléfonos; ficha propia de contacto con llamada, WhatsApp,
  correo y tarjeta para la agenda del teléfono.
- Próximas acciones compartidas entre varias personas (también por MCP: parámetro `also`).
- Historial de cambios del lead, actividad del equipo y altas recientes en **Hoy**.
- «Buscar en internet» para rellenar las fichas de empresa y de contacto con la IA configurada.
- NDA con el cliente desde la ficha del lead, en español o en inglés, con tu empresa como parte firmante
  (`NDA_ISSUER_*`).
- Precios del catálogo desde Stripe: cuota mensual, anual y tramos por usuario (`STRIPE_SECRET_KEY`,
  `sync_stripe`).
- Reuniones de Teams convocadas como tarjeta con botón para unirse.
- Experiencia de app en el móvil: barra superior, pestañas, botón ＋, hoja «Más», buscador único y transiciones
  entre pantallas. En Android, accesos directos del icono y «Compartir» hacia el CRM.
- Dirección y dirección general pueden crear y gestionar ferias y eventos.
- Prefijo `[QopenCRM]` con empresa y oportunidad en el asunto de los correos internos.

### Cambiado

- El menú **Pipeline** pasa a llamarse **Leads**.
- Los formularios rápidos de empresa y contacto muestran todos los campos.

### Corregido

- El servidor MCP responde en `/mcp` sin barra final en lugar de redirigir.
- El selector de menciones ya no queda recortado por el contenedor.
- Mensaje claro cuando falta el permiso de aplicación para añadir un buzón de Microsoft 365.

## 0.1.0 — 2026-11-25

Primera versión pública.

### Añadido

- Pipeline en tablero y en lista, con etapas configurables, próxima acción obligatoria, oportunidades paradas a la
  espera del cliente y motivos de pérdida.
- Empresas y contactos con deduplicación, fusión, importación CSV y exportación.
- Tareas con recordatorios por correo y resumen diario.
- Presupuestos en PDF versionados, con plantillas, logo propio (`branding/logo.png`), prefijo de numeración
  configurable (`QUOTE_NUMBER_PREFIX`) y envío por correo.
- Catálogo de productos con cobro recurrente, por hora o pago único; suscripciones y bolsas de horas.
- Informes de dirección, ferias y eventos, incentivos y banco de conocimiento en Markdown.
- Servidor MCP con 43 herramientas y autorización OAuth 2.1, además de claves de API personales.
- «Rellenar con IA» y búsqueda en lenguaje natural con OpenAI o Anthropic.
- API REST con OpenAPI y webhooks firmados con HMAC-SHA256.
- Captura automática de correo por IMAP y reuniones de Teams con Microsoft Graph.
- Acceso sin contraseñas, roles configurables, registro de accesos y herramientas de RGPD.
- PWA y proyecto de app Android (Trusted Web Activity).
- `qopencrm.sh`: instalación, actualización, copias (local, FTP, FTPS, SFTP) y restauración.
- Datos de demostración: `./qopencrm.sh demo`.
