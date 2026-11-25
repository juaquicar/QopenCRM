# Instalación

QopenCRM se instala con Docker en un solo comando. Esta guía sirve tanto para probarlo en tu portátil como para
dejarlo funcionando en un servidor; lo específico de producción (HTTPS, copias programadas) está en
[DESPLIEGUE.md](DESPLIEGUE.md).

## Requisitos

- Linux o macOS (en Windows, WSL 2).
- [Docker](https://docs.docker.com/engine/install/) con el complemento Compose (`docker compose version`).
- `git`, `openssl` y `curl`. Vienen de serie en casi cualquier distribución.
- 1 GB de RAM y 2 GB de disco libres.

## Instalar

```bash
git clone https://github.com/juaquicar/QopenCRM.git
cd QopenCRM
./qopencrm.sh install
```

El instalador pregunta tres cosas:

1. **URL pública del CRM.** Para probar, deja `http://localhost:8000`. En un servidor, `https://crm.tu-dominio.com`.
2. **Puerto local** de la web (8000 por defecto).
3. **Correo del primer superusuario.** Es quien después da de alta al resto del equipo.

Y hace el resto: crea `.env` con una clave secreta y una contraseña de base de datos aleatorias, construye la imagen,
arranca los servicios, aplica las migraciones y carga los datos iniciales (roles, etapas, motivos de pérdida y una
plantilla de presupuesto).

Para instalar sin preguntas, por ejemplo desde un script:

```bash
ADMIN_EMAIL=tu@correo.example SITE_URL=http://localhost:8000 WEB_PORT=8000 ./qopencrm.sh install --yes
```

## Primer acceso

Abre la URL y escribe tu correo. QopenCRM no usa contraseñas: envía un código de 6 cifras que caduca a los 10 minutos.

Hasta que configures el correo saliente, el código no se envía: sale en los registros.

```bash
./qopencrm.sh logs web | grep 'código de acceso'
```

Para que llegue por correo, configura SMTP en `.env` (ver [CONFIGURACION.md](CONFIGURACION.md#correo-saliente)) y
ejecuta `./qopencrm.sh restart`.

## Datos de demostración (opcional)

```bash
./qopencrm.sh demo
```

Carga un equipo de tres personas, un catálogo, ocho empresas y un pipeline repartido por todas las etapas, con
presupuestos, tareas, una oportunidad parada y una ganada. Todo es ficticio (dominios `.example`). Solo funciona en una
instalación sin empresas, para que no se mezcle con datos reales.

Puedes entrar como `julia@demo.example` (responsable comercial), `luis@demo.example` o `ana@demo.example`
(comerciales); el código sale en los registros.

## Primeros pasos dentro del CRM

1. **Ajustes → Usuarios:** da de alta a las personas y asígnales un rol.
2. **Ajustes → Plantillas de presupuesto:** pon tu razón social, dirección y pie (IBAN, CIF).
3. **`branding/logo.png`:** deja ahí tu logo para los presupuestos en PDF.
4. **Productos:** crea tu catálogo.
5. **Ajustes → Datos → Importar:** sube un CSV con tus empresas y contactos. Se comprueba antes de guardar y no duplica.
6. **Manual:** dentro de la aplicación, adaptado a lo que puede hacer cada rol.

## Operación diaria

```bash
./qopencrm.sh status            # estado de los servicios
./qopencrm.sh logs [servicio]   # registros en vivo (web, worker, db)
./qopencrm.sh stop | start | restart
./qopencrm.sh backup            # copia local de base de datos y ficheros
./qopencrm.sh update            # git pull, copia previa, reconstruye y migra
./qopencrm.sh help              # todos los comandos
```

## Servicios

| Servicio | Qué hace |
|---|---|
| `db` | PostgreSQL 18. Solo escucha en `127.0.0.1`. |
| `web` | La aplicación (gunicorn). Solo escucha en `127.0.0.1:8000`. |
| `worker` | Cada 5 minutos: recordatorios de tareas, reintentos de webhooks y captura de correo. A las 8:00, de lunes a viernes, el resumen diario. |
| `caddy` | Solo si `SITE_URL` es `https://`: proxy inverso con certificado automático. |

## Desinstalar

```bash
./qopencrm.sh uninstall           # borra contenedores e imagen; hace una última copia
./qopencrm.sh uninstall --purge   # además borra los datos
```

## Problemas habituales

| Síntoma | Causa y arreglo |
|---|---|
| «No se puede hablar con Docker» | Docker parado o tu usuario no está en el grupo `docker`: `sudo usermod -aG docker $USER` y vuelve a entrar. |
| El puerto 8000 está ocupado | Cambia `WEB_PORT` en `.env` y `./qopencrm.sh restart`. |
| El puerto 5433 está ocupado | Cambia `DB_HOST_PORT` en `.env`. |
| No llega el código de acceso | Sin SMTP sale en los registros. Con SMTP, revisa `EMAIL_*` y `./qopencrm.sh logs web`. |
| «CSRF verification failed» | `SITE_URL` no coincide con la dirección que usas en el navegador. |
