## Qué cambia y por qué

<!-- El problema que resuelve. Enlaza la incidencia: «Closes #123». -->

## Cómo probarlo

<!-- Pasos para comprobarlo a mano. Capturas de antes y después si cambia algo visible. -->

## Comprobaciones

- [ ] `uv run python manage.py test apps` pasa
- [ ] `uv run python manage.py makemigrations --check` no detecta cambios pendientes
- [ ] Hay prueba para el cambio
- [ ] Lo nuevo respeta los permisos por rol (`scope()` y `can_edit()`), también por API y MCP
- [ ] Documentación y `CHANGELOG.md` actualizados, si procede
- [ ] No incluye datos reales, claves ni secretos
