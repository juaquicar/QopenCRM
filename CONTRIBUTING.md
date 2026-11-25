# Contribuir a QopenCRM

Gracias por querer mejorar QopenCRM. Toda ayuda cuenta: avisar de un error, proponer una idea, corregir la
documentación, traducir o enviar código.

Al participar aceptas el [código de conducta](CODE_OF_CONDUCT.md).

## Avisar de un error o proponer una mejora

- **Errores:** abre una [incidencia](https://github.com/juaquicar/QopenCRM/issues/new/choose) con los pasos para
  reproducirlo, lo que esperabas y lo que pasó. Indica la versión (`git rev-parse --short HEAD`) y, si puedes, lo que
  sale en `./qopencrm.sh logs web`.
- **Ideas:** cuenta primero el problema que quieres resolver; después, la solución que imaginas.
- **Vulnerabilidades:** no abras una incidencia pública. Sigue [SECURITY.md](SECURITY.md).

Antes de abrir nada, busca por si ya existe. No pegues datos reales de clientes ni claves en incidencias o PR.

## Enviar código

1. Para cambios grandes, abre antes una incidencia y lo hablamos: evita trabajo que luego no encaje.
2. Haz un *fork* y crea una rama desde `main`: `git switch -c fix/descripcion-corta`.
3. Monta el entorno como indica [docs/DESARROLLO.md](docs/DESARROLLO.md).
4. Haz el cambio **con su prueba**.
5. Comprueba que todo pasa:

   ```bash
   uv run python manage.py test apps
   uv run python manage.py makemigrations --check
   ```

6. Anota el cambio en [CHANGELOG.md](CHANGELOG.md), bajo «Sin publicar», si lo nota quien usa el CRM.
7. Abre la *pull request* contra `main` y rellena la plantilla.

### Qué esperamos de una PR

- **Un solo propósito.** Mejor dos PR pequeñas que una grande.
- **Permisos respetados.** Toda consulta nueva pasa por `scope()` y toda edición por `can_edit()`; lo que se pueda
  hacer en la web debe poder hacerse, con los mismos límites, por API y por MCP.
- **Sin dependencias nuevas** salvo que sean imprescindibles; explícalo en la PR.
- **Sin paso de compilación** en el frontend: HTML, htmx y JavaScript sin transpilar.
- **Interfaz y documentación en español**; identificadores de código en inglés.
- **Capturas** de antes y después si cambia algo visible.

### Mensajes de commit

Formato [Conventional Commits](https://www.conventionalcommits.org/es/), en inglés o en español, en imperativo:

```
feat: create the company from the new-contact dialog
fix: el resumen diario no se enviaba en festivos
docs: explain webhook signature verification
```

Tipos habituales: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`.

## Buenas primeras aportaciones

- Incidencias con la etiqueta [`good first issue`](https://github.com/juaquicar/QopenCRM/labels/good%20first%20issue).
- **Traducciones:** la interfaz está solo en español. Preparar las plantillas para `gettext` es un trabajo muy
  bienvenido; coméntalo antes en una incidencia para repartirlo.
- **Documentación:** si algo de la instalación no te quedó claro, a la siguiente persona tampoco.
- **Herramientas MCP** nuevas: [docs/DESARROLLO.md](docs/DESARROLLO.md#añadir-una-herramienta-mcp).

## Etiquetas

| Etiqueta | Uso |
|---|---|
| `bug` | Algo no funciona como debería. |
| `enhancement` | Función nueva o mejora. |
| `documentation` | Documentación. |
| `good first issue` | Buena para empezar. |
| `help wanted` | Se agradece ayuda. |
| `ai` | Servidor MCP y funciones de IA. |
| `api` | API REST y webhooks. |
| `i18n` | Traducciones e internacionalización. |
| `security` | Endurecimiento (las vulnerabilidades, por el canal privado). |
| `ux` | Interfaz y experiencia de uso. |
| `deploy` | Instalación, Docker, copias y actualización. |

## Licencia

Al contribuir aceptas que tu aportación se publique bajo la [licencia MIT](LICENSE) del proyecto.
