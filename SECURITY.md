# Política de seguridad

QopenCRM guarda datos comerciales y personales. Nos tomamos en serio cualquier fallo que los ponga en riesgo.

## Avisar de una vulnerabilidad

**No abras una incidencia pública.** Usa el aviso privado de GitHub:

1. Ve a la pestaña [**Security**](https://github.com/juaquicar/QopenCRM/security) del repositorio.
2. Pulsa **Report a vulnerability**.
3. Describe el fallo, cómo reproducirlo, a qué versión o commit afecta y qué impacto le ves.

Recibirás respuesta en un plazo orientativo de 7 días. Una vez corregido, se publicará un aviso y, si quieres, se te
reconocerá el hallazgo.

Por favor, no pruebes contra instalaciones que no sean tuyas ni accedas a datos de terceros.

## Versiones con soporte

Se corrige la rama `main` y la última versión publicada.

## Qué entra

- Saltarse la autenticación (códigos de acceso, claves de API, OAuth del servidor MCP).
- Saltarse los permisos: ver o cambiar registros fuera del alcance del rol, por web, API o MCP.
- Inyección (SQL, plantillas, HTML en el banco de conocimiento o en los PDF), XSS, CSRF y SSRF.
- Fuga de secretos, de tokens de integraciones o de datos personales.
- Falsificación de firmas de webhooks.

## Qué no entra

- Configuraciones inseguras elegidas por quien despliega (`DJANGO_DEBUG=1` en producción, sin HTTPS, `.env` expuesto).
- Lo que un asistente de IA haga con datos a los que la persona conectada ya tiene acceso legítimo.
- Ataques que exijan acceso previo al servidor o a la base de datos.

## Si despliegas QopenCRM

Sigue la [lista de comprobación de seguridad](docs/DESPLIEGUE.md#lista-de-comprobación-de-seguridad) y mantén la
instalación al día con `./qopencrm.sh update`.
