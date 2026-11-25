# Despliegue en producción

## Lo mínimo

- Un servidor Linux con Docker y Compose, y los puertos 80 y 443 abiertos.
- Un nombre de dominio que apunte al servidor (registro A o AAAA).
- Una cuenta SMTP para el correo saliente.

```bash
git clone https://github.com/juaquicar/QopenCRM.git /opt/qopencrm
cd /opt/qopencrm
./qopencrm.sh install        # URL pública: https://crm.tu-dominio.com
```

Con una `SITE_URL` que empieza por `https://` el script añade solo un proxy inverso **Caddy**, que pide y renueva el
certificado de Let's Encrypt, redirige de HTTP a HTTPS y reenvía a la web. Además, Django activa cookies seguras y
HSTS.

Configura el correo saliente en `.env` ([CONFIGURACION.md](CONFIGURACION.md#correo-saliente)) y reinicia:

```bash
./qopencrm.sh restart
```

## Si ya tienes un proxy inverso

Pon `PROXY=0` en `.env` y apunta tu proxy (Nginx, Traefik, otro Caddy) a `127.0.0.1:8000`. El proxy tiene que enviar
`X-Forwarded-Proto: https` y conservar la cabecera `Host`. Ejemplo con Nginx:

```nginx
server {
    listen 443 ssl;
    server_name crm.tu-dominio.com;
    # ssl_certificate ...; ssl_certificate_key ...;
    client_max_body_size 20m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

## Copias de seguridad

Una copia es un único fichero `backups/qopencrm-FECHA.tar.gz` con el volcado de PostgreSQL y los ficheros (los PDF de
los presupuestos).

```bash
./qopencrm.sh backup              # copia local
./qopencrm.sh backup-ftp          # copia local y subida al servidor remoto de .env
./qopencrm.sh list [--ftp]        # copias disponibles
./qopencrm.sh cron 03:15 backup-ftp   # programa la copia diaria
./qopencrm.sh cron-remove
```

Restaurar **sustituye todo lo actual** y pide confirmación:

```bash
./qopencrm.sh restore latest
./qopencrm.sh restore backups/qopencrm-20260101-031500.tar.gz
./qopencrm.sh restore-ftp latest
```

Recomendaciones:

- Guarda las copias fuera del servidor (`backup-ftp`, con `ftps` o `sftp`).
- Guarda también `.env` en un lugar seguro: sin `DJANGO_SECRET_KEY` no se pueden descifrar los tokens de las
  integraciones.
- Prueba a restaurar una copia de vez en cuando en otra máquina.

## Actualizar

```bash
./qopencrm.sh update
```

Hace `git pull`, una copia de seguridad previa, reconstruye la imagen y aplica las migraciones. Antes de actualizar,
lee [CHANGELOG.md](../CHANGELOG.md). Con `--no-pull` reconstruye sin descargar cambios.

Si algo va mal: `git checkout <versión anterior>`, `./qopencrm.sh restore latest` y `./qopencrm.sh update --no-pull`.

## Lista de comprobación de seguridad

- [ ] `SITE_URL` con `https://` y `DJANGO_DEBUG=0`.
- [ ] `.env` con permisos `600` y fuera de cualquier copia pública.
- [ ] Solo los puertos 80 y 443 abiertos; la web y la base de datos escuchan en `127.0.0.1`.
- [ ] SMTP con SPF y DKIM en tu dominio, para que los códigos de acceso no acaben en correo no deseado.
- [ ] Copias diarias fuera del servidor y una restauración probada.
- [ ] Roles revisados en **Ajustes → Usuarios**: quién ve todo, quién exporta, quién borra y qué herramientas MCP tiene
      cada rol.
- [ ] Sistema operativo y Docker con actualizaciones de seguridad automáticas.

## Móvil

QopenCRM es una PWA: desde el navegador del móvil, «Añadir a pantalla de inicio» la instala a pantalla completa.

### App Android

El repositorio incluye un proyecto Android mínimo (una *Trusted Web Activity*: tu web, sin barra del navegador). No se
distribuye ningún APK: cada instalación compila y firma el suyo, porque va ligado a su dominio.

```bash
CRM_HOST=crm.tu-dominio.com android/build.sh 1.0.0
```

Necesita Docker y el SDK de Android. La primera vez crea la clave de firma en `android/keystore/` (**guárdala**: sin
ella no podrás publicar actualizaciones). Al terminar imprime la huella SHA-256: ponla en `ANDROID_SHA256` de `.env` y
ejecuta `./qopencrm.sh update --no-pull`. El APK queda en `android/dist/` y el CRM lo ofrece para descargar, con la
sesión iniciada, en **Ajustes → App Android**.
