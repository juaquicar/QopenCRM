# Cambios

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/) y
[versionado semántico](https://semver.org/lang/es/).

## Sin publicar

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
