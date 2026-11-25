#!/usr/bin/env bash
# QopenCRM — instalación, operación y copias de seguridad.
# Uso: ./qopencrm.sh <comando> [opciones]      (./qopencrm.sh help para verlos todos)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
ENV_FILE="$ROOT/.env"

c_ok=$'\033[32m'; c_warn=$'\033[33m'; c_bad=$'\033[31m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
[ -t 1 ] || { c_ok=""; c_warn=""; c_bad=""; c_dim=""; c_off=""; }
say()  { printf '%s\n' "${c_ok}▸${c_off} $*"; }
warn() { printf '%s\n' "${c_warn}!${c_off} $*" >&2; }
die()  { printf '%s\n' "${c_bad}✗${c_off} $*" >&2; exit 1; }

# temporales (volcados, credenciales FTP): se borran siempre al salir
CLEANUP=()
cleanup() { local item; for item in "${CLEANUP[@]:-}"; do if [ -n "$item" ]; then rm -rf "$item"; fi; done; return 0; }
trap cleanup EXIT

# Con SITE_URL en https se añade el proxy inverso con TLS (Caddy). PROXY=0 lo desactiva si ya hay otro proxy delante.
dc() {
  local url files=(-f "$ROOT/docker-compose.yml")
  url="$(env_get SITE_URL)"
  if [[ "$url" == https://* ]] && [ "$(env_get PROXY)" != "0" ]; then
    files+=(-f "$ROOT/docker-compose.prod.yml")
    SITE_HOST="$(printf '%s' "$url" | sed -E 's#^https://##; s#[:/].*$##')"; export SITE_HOST
  fi
  docker compose --project-directory "$ROOT" "${files[@]}" "$@"
}

need_docker() {
  command -v docker >/dev/null 2>&1 || die "Docker no está instalado. Instálalo: https://docs.docker.com/engine/install/"
  docker compose version >/dev/null 2>&1 || die "Falta el plugin «docker compose» (v2)."
  docker info >/dev/null 2>&1 || die "No se puede hablar con Docker. ¿Está arrancado? ¿Tu usuario está en el grupo docker?"
}

# Lee una variable del entorno o del .env sin ejecutar el fichero (admite valores con espacios y comillas)
env_get() {
  if [ -n "${!1:-}" ]; then printf '%s' "${!1}"; return 0; fi  # el entorno manda sobre .env
  [ -f "$ENV_FILE" ] || return 0
  local line value
  line="$(grep -E "^$1=" "$ENV_FILE" | tail -n1 || true)"
  value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

env_set() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp)"
  if grep -qE "^$key=" "$ENV_FILE" 2>/dev/null; then
    awk -v k="$key" -v v="$value" 'BEGIN{FS=OFS="="} $1==k {print k "=" v; next} {print}' "$ENV_FILE" > "$tmp"
  else
    { cat "$ENV_FILE" 2>/dev/null || true; printf '%s=%s\n' "$key" "$value"; } > "$tmp"
  fi
  cat "$tmp" > "$ENV_FILE"; rm -f "$tmp"
}

need_env() { [ -f "$ENV_FILE" ] || die "No hay .env. Ejecuta primero: ./qopencrm.sh install"; }

confirm() {  # confirm "pregunta" [palabra-exigida]
  [ "${ASSUME_YES:-0}" = "1" ] && return 0
  [ -t 0 ] || die "Hace falta confirmación. Repite con --yes si estás seguro."
  local answer
  if [ -n "${2:-}" ]; then
    read -r -p "$1 Escribe «$2» para continuar: " answer
    [ "$answer" = "$2" ]
  else
    read -r -p "$1 [s/N] " answer
    [[ "$answer" =~ ^[sSyY]$ ]]
  fi
}

ask() {  # ask "pregunta" "valor por defecto"
  local answer=""
  if [ -t 0 ] && [ "${ASSUME_YES:-0}" != "1" ]; then read -r -p "$1 [$2]: " answer; fi
  printf '%s' "${answer:-$2}"
}

wait_healthy() {
  say "Esperando a que la web responda…"
  local i status
  for i in $(seq 1 90); do
    status="$(docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q web 2>/dev/null)" 2>/dev/null || true)"
    [ "$status" = "healthy" ] && return 0
    [ "$status" = "unhealthy" ] && break
    sleep 2
  done
  dc logs --tail 40 web >&2 || true
  die "La web no ha arrancado. Revisa los registros de arriba (./qopencrm.sh logs web)."
}

# ----------------------------------------------------------------- instalar
cmd_install() {
  need_docker
  if [ ! -f "$ENV_FILE" ]; then
    say "Creando la configuración (.env)"
    cp "$ROOT/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    env_set DJANGO_SECRET_KEY "'$(openssl rand -base64 48 | tr -d '\n=+/' | cut -c1-50)'"
    env_set POSTGRES_PASSWORD "$(openssl rand -hex 20)"
    local url host port
    url="$(ask "URL pública del CRM" "${SITE_URL:-http://localhost:8000}")"
    host="$(printf '%s' "$url" | sed -E 's#^https?://##; s#[:/].*$##')"
    port="$(ask "Puerto local para la web" "${WEB_PORT:-8000}")"
    env_set SITE_URL "$url"
    env_set DJANGO_ALLOWED_HOSTS "$host,localhost,127.0.0.1"
    env_set WEB_PORT "$port"
    env_set DJANGO_DEBUG 0
    warn "Correo: los códigos de acceso salen por los registros hasta que configures SMTP en .env"
    warn "       (EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend, EMAIL_HOST, EMAIL_HOST_USER…)"
  else
    say "Ya existe .env: se conserva"
    chmod 600 "$ENV_FILE"
  fi
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  say "Construyendo la imagen"
  dc build
  say "Arrancando servicios"
  dc up -d
  wait_healthy
  local admin="${ADMIN_EMAIL:-}"
  if [ -z "$admin" ] && [ "$(dc exec -T web python manage.py shell -c 'from apps.accounts.models import User; print(User.objects.filter(is_superuser=True).count())' 2>/dev/null | tail -n1)" = "0" ]; then
    admin="$(ask "Correo del primer superusuario" "")"
  fi
  [ -n "$admin" ] && dc exec -T web python manage.py seed --admin "$admin" --name "${ADMIN_NAME:-}"
  say "Instalado. Abre $(env_get SITE_URL)"
  [[ "$(env_get EMAIL_BACKEND)" == *console* ]] && say "Código de acceso (sin SMTP): ./qopencrm.sh logs web | grep 'código de acceso'"
  return 0
}

cmd_update() {
  need_docker; need_env
  if [ -d "$ROOT/.git" ] && [ "${1:-}" != "--no-pull" ]; then say "Descargando cambios"; git -C "$ROOT" pull --ff-only; fi
  say "Copia de seguridad previa"
  cmd_backup
  say "Reconstruyendo"
  dc build
  dc up -d
  wait_healthy
  say "Actualizado"
}

# ------------------------------------------------------------------- copias
cmd_backup() {
  need_docker; need_env
  [ -n "$(dc ps -q db 2>/dev/null)" ] || die "La base de datos no está en marcha (./qopencrm.sh start)."
  local stamp work file user db
  stamp="$(date +%Y%m%d-%H%M%S)"
  user="$(env_get POSTGRES_USER)"; db="$(env_get POSTGRES_DB)"
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  work="$(mktemp -d)"; CLEANUP+=("$work")
  say "Volcando la base de datos"
  dc exec -T db pg_dump -U "$user" -d "$db" --format=custom --no-owner --no-acl > "$work/db.dump"
  [ -s "$work/db.dump" ] || die "El volcado ha salido vacío."
  say "Guardando ficheros (presupuestos en PDF)"
  if [ -n "$(dc ps -q web 2>/dev/null)" ]; then
    dc exec -T web tar -C /app/media -cf - . > "$work/media.tar"
  else
    dc run --rm -T --no-deps --entrypoint tar web -C /app/media -cf - . > "$work/media.tar"
  fi
  printf 'QopenCRM\nfecha=%s\nbase=%s\ncommit=%s\n' "$stamp" "$db" "$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo -)" > "$work/INFO"
  file="$BACKUP_DIR/qopencrm-$stamp.tar.gz"
  ( umask 077; tar -C "$work" -czf "$file" db.dump media.tar INFO )
  tar -tzf "$file" >/dev/null || die "La copia no se puede leer: $file"
  LAST_BACKUP="$file"
  say "Copia creada: $file ($(du -h "$file" | cut -f1))"
  local keep
  keep="$(env_get BACKUP_KEEP)"; keep="${keep:-14}"
  if [ "$keep" -gt 0 ] 2>/dev/null; then
    ls -1t "$BACKUP_DIR"/qopencrm-*.tar.gz 2>/dev/null | tail -n +"$((keep + 1))" | while read -r old; do rm -f "$old"; say "Copia antigua borrada: $(basename "$old")"; done
  fi
}

# Prepara la conexión FTP. La contraseña va en un fichero temporal, nunca en la línea de órdenes.
ftp_setup() {
  command -v curl >/dev/null 2>&1 || die "Hace falta curl para la copia remota."
  FTP_PROTOCOL="$(env_get FTP_PROTOCOL)"; FTP_PROTOCOL="${FTP_PROTOCOL:-ftps}"
  FTP_HOST="$(env_get FTP_HOST)"; FTP_PORT="$(env_get FTP_PORT)"; FTP_USER="$(env_get FTP_USER)"
  FTP_PASSWORD="$(env_get FTP_PASSWORD)"; FTP_DIR="$(env_get FTP_DIR)"; FTP_DIR="/${FTP_DIR#/}"; FTP_DIR="${FTP_DIR%/}"
  [ -n "$FTP_HOST" ] && [ -n "$FTP_USER" ] || die "Configura FTP_HOST, FTP_USER y FTP_PASSWORD en .env"
  case "$FTP_PROTOCOL" in
    ftp)  FTP_SCHEME="ftp";  FTP_OPTS=() ; warn "FTP sin cifrar: la copia y la contraseña viajan en claro. Usa ftps o sftp si el servidor lo admite." ;;
    ftps) FTP_SCHEME="ftp";  FTP_OPTS=(--ssl-reqd) ;;
    sftp) FTP_SCHEME="sftp"; FTP_OPTS=() ;;
    *) die "FTP_PROTOCOL debe ser ftp, ftps o sftp" ;;
  esac
  FTP_BASE="$FTP_SCHEME://$FTP_HOST${FTP_PORT:+:$FTP_PORT}$FTP_DIR/"
  FTP_AUTH="$(mktemp)"; chmod 600 "$FTP_AUTH"
  CLEANUP+=("$FTP_AUTH")
  local credentials="$FTP_USER:$FTP_PASSWORD"
  credentials="${credentials//\\/\\\\}"; credentials="${credentials//\"/\\\"}"
  printf 'user = "%s"\n' "$credentials" > "$FTP_AUTH"
}
ftp_curl() { curl --silent --show-error --fail --config "$FTP_AUTH" --connect-timeout 20 "${FTP_OPTS[@]}" "$@"; }
ftp_list() { ftp_curl --list-only "$FTP_BASE" | tr -d '\r' | grep -E '^qopencrm-[0-9]{8}-[0-9]{6}\.tar\.gz$' | sort; }

cmd_backup_ftp() {
  need_env
  ftp_setup
  cmd_backup
  local name; name="$(basename "$LAST_BACKUP")"
  say "Subiendo a $FTP_PROTOCOL://$FTP_HOST$FTP_DIR/"
  ftp_curl --ftp-create-dirs --upload-file "$LAST_BACKUP" "$FTP_BASE$name"
  # sin tubería: con pipefail, grep -q corta la orden anterior (SIGPIPE) y la comprobación falla en falso
  grep -qx "$name" <<< "$(ftp_list)" || die "La copia no aparece en el servidor remoto tras subirla."
  say "Copia remota hecha: $name"
  local keep; keep="$(env_get FTP_KEEP)"; keep="${keep:-0}"
  if [ "$keep" -gt 0 ] 2>/dev/null; then
    ftp_list | head -n -"$keep" | while read -r old; do
      if [ "$FTP_SCHEME" = "sftp" ]; then ftp_curl -Q "rm $FTP_DIR/$old" "$FTP_BASE" >/dev/null; else ftp_curl -Q "DELE $FTP_DIR/$old" "$FTP_BASE" >/dev/null; fi
      say "Copia remota antigua borrada: $old"
    done
  fi
}

cmd_list() {
  say "Copias locales en $BACKUP_DIR"
  ls -1t "$BACKUP_DIR"/qopencrm-*.tar.gz 2>/dev/null | while read -r f; do printf '  %s  %s\n' "$(du -h "$f" | cut -f1)" "$(basename "$f")"; done
  if [ "${1:-}" = "--ftp" ]; then need_env; ftp_setup; say "Copias remotas en $FTP_HOST$FTP_DIR"; ftp_list | sed 's/^/  /'; fi
}

cmd_restore() {
  need_docker; need_env
  local file="${1:-}"
  [ -n "$file" ] || die "Indica la copia: ./qopencrm.sh restore <fichero|latest>   (./qopencrm.sh list para verlas)"
  if [ "$file" = "latest" ]; then file="$(ls -1t "$BACKUP_DIR"/qopencrm-*.tar.gz 2>/dev/null | head -n1 || true)"; fi
  [ -f "$file" ] || { [ -f "$BACKUP_DIR/$file" ] && file="$BACKUP_DIR/$file"; }
  [ -f "$file" ] || die "No encuentro la copia: $file"
  grep -qx 'db.dump' <<< "$(tar -tzf "$file")" || die "El fichero no es una copia de QopenCRM."
  warn "Se va a SUSTITUIR toda la base de datos y los ficheros actuales por los de: $(basename "$file")"
  confirm "Esto no se puede deshacer." "restaurar" || die "Cancelado."
  local user db work
  user="$(env_get POSTGRES_USER)"; db="$(env_get POSTGRES_DB)"
  work="$(mktemp -d)"; CLEANUP+=("$work")
  tar -C "$work" -xzf "$file"
  if [ "${SKIP_SAFETY_BACKUP:-0}" != "1" ] && [ -n "$(dc ps -q db 2>/dev/null)" ]; then
    say "Copia de seguridad del estado actual, por si acaso"
    ( cmd_backup ) || warn "No se ha podido hacer la copia previa."
  fi
  say "Parando web y worker"
  dc stop web worker >/dev/null 2>&1 || true
  dc up -d db >/dev/null
  until dc exec -T db pg_isready -U "$user" -d postgres >/dev/null 2>&1; do sleep 1; done
  say "Restaurando la base de datos"
  dc exec -T db dropdb -U "$user" --if-exists --force "$db"
  dc exec -T db createdb -U "$user" "$db"
  dc exec -T db pg_restore -U "$user" -d "$db" --no-owner --no-acl --exit-on-error < "$work/db.dump"
  if [ -f "$work/media.tar" ]; then
    say "Restaurando ficheros"
    dc run --rm -T --no-deps --entrypoint sh web -c 'find /app/media -mindepth 1 -delete; tar -C /app/media -xf -' < "$work/media.tar"
  fi
  say "Arrancando"
  dc up -d
  wait_healthy
  say "Restaurado desde $(basename "$file")"
}

cmd_restore_ftp() {
  need_env
  ftp_setup
  local name="${1:-latest}"
  [ "$name" = "latest" ] && name="$(ftp_list | tail -n1)"
  [ -n "$name" ] || die "No hay copias en el servidor remoto."
  mkdir -p "$BACKUP_DIR"; chmod 700 "$BACKUP_DIR"
  say "Descargando $name"
  ( umask 077; ftp_curl --output "$BACKUP_DIR/$name" "$FTP_BASE$name" )
  cmd_restore "$BACKUP_DIR/$name"
}

cmd_cron() {
  need_env
  local when="${1:-03:15}" mode="${2:-backup-ftp}" hour minute line
  [[ "$when" =~ ^([01][0-9]|2[0-3]):([0-5][0-9])$ ]] || die "Hora no válida. Ejemplo: ./qopencrm.sh cron 03:15 backup-ftp"
  [[ "$mode" == "backup" || "$mode" == "backup-ftp" ]] || die "El segundo argumento debe ser backup o backup-ftp"
  hour="${BASH_REMATCH[1]}"; minute="${BASH_REMATCH[2]}"
  line="$((10#$minute)) $((10#$hour)) * * * $ROOT/qopencrm.sh $mode >> $BACKUP_DIR/backup.log 2>&1 # qopencrm-backup"
  mkdir -p "$BACKUP_DIR"
  ( crontab -l 2>/dev/null | grep -v '# qopencrm-backup' || true; echo "$line" ) | crontab -
  say "Copia diaria programada a las $when ($mode). Registro: $BACKUP_DIR/backup.log"
}

cmd_cron_remove() {
  ( crontab -l 2>/dev/null | grep -v '# qopencrm-backup' || true ) | crontab -
  say "Copia programada eliminada"
}

# ---------------------------------------------------------------- desinstalar
cmd_uninstall() {
  need_docker
  local purge=0
  [ "${1:-}" = "--purge" ] && purge=1
  warn "Se van a borrar los contenedores, la imagen y TODOS los datos de QopenCRM (base de datos y ficheros)."
  [ "$purge" = "1" ] && warn "Con --purge se borran además .env y la carpeta de copias locales."
  confirm "Esto no se puede deshacer." "desinstalar" || die "Cancelado."
  if [ -f "$ENV_FILE" ] && [ -n "$(dc ps -q db 2>/dev/null)" ] && [ "${SKIP_SAFETY_BACKUP:-0}" != "1" ]; then
    say "Última copia de seguridad antes de borrar"
    cmd_backup || warn "No se ha podido hacer la copia final."
  fi
  local temp_env=0
  if [ ! -f "$ENV_FILE" ]; then cp "$ROOT/.env.example" "$ENV_FILE"; temp_env=1; fi  # compose necesita un .env para poder desmontar
  dc down --volumes --rmi local --remove-orphans || true
  [ "$temp_env" = "1" ] && rm -f "$ENV_FILE"
  cmd_cron_remove >/dev/null 2>&1 || true
  if [ "$purge" = "1" ]; then
    rm -rf "$BACKUP_DIR" "$ENV_FILE"
    say "Desinstalado por completo."
  else
    say "Desinstalado. Se conservan .env y las copias en $BACKUP_DIR (bórralos con --purge)."
  fi
}

# ------------------------------------------------------------------ operación
cmd_help() {
  cat <<EOF
QopenCRM — gestión

  Instalación
    install                 Crea .env, construye la imagen, arranca todo y crea el superusuario
                            (sin preguntas: ADMIN_EMAIL=… SITE_URL=… WEB_PORT=… ./qopencrm.sh install --yes)
    update [--no-pull]      git pull, copia de seguridad, reconstruye y aplica migraciones
    uninstall [--purge]     Borra contenedores, imagen y datos (hace una última copia antes)

  Operación
    start | stop | restart  Arranca, para o reinicia los servicios
    status                  Estado de los servicios
    logs [servicio]         Registros en vivo (web, worker, db)
    manage <orden…>         Ejecuta manage.py dentro del contenedor (p. ej. manage createsuperuser)
    admin <correo> [nombre] Crea o asciende un superusuario
    shell                   Consola de Django
    psql                    Consola de PostgreSQL
    test                    Ejecuta la batería de pruebas
    demo                    Carga datos de demostración ficticios (solo en una instalación vacía)

  Copias de seguridad
    backup                  Copia local: base de datos + ficheros → backups/qopencrm-FECHA.tar.gz
    backup-ftp              Copia local y subida al servidor FTP/FTPS/SFTP configurado en .env
    list [--ftp]            Lista las copias locales (y remotas)
    restore <fichero|latest>   Restaura una copia local (sustituye todo lo actual)
    restore-ftp [nombre|latest] Descarga una copia remota y la restaura
    cron [HH:MM] [backup|backup-ftp]  Programa la copia diaria (por defecto 03:15, backup-ftp)
    cron-remove             Quita la copia programada

  Opciones: --yes  no pedir confirmación (para scripts)
EOF
}

ARGS=()
for arg in "$@"; do
  if [ "$arg" = "--yes" ] || [ "$arg" = "-y" ]; then ASSUME_YES=1; else ARGS+=("$arg"); fi
done
set -- "${ARGS[@]:-help}"
command="$1"; shift || true

case "$command" in
  install)      cmd_install "$@" ;;
  update)       cmd_update "$@" ;;
  uninstall)    cmd_uninstall "$@" ;;
  start)        need_docker; need_env; dc up -d; wait_healthy; say "En marcha: $(env_get SITE_URL)" ;;
  stop)         need_docker; dc stop; say "Parado" ;;
  restart)      need_docker; need_env; dc restart; wait_healthy; say "Reiniciado" ;;
  status)       need_docker; dc ps ;;
  logs)         need_docker; dc logs -f --tail 100 "$@" ;;
  manage)       need_docker; need_env; dc exec web python manage.py "$@" ;;
  admin)        need_docker; need_env; [ -n "${1:-}" ] || die "Uso: ./qopencrm.sh admin <correo> [nombre]"; dc exec -T web python manage.py seed --admin "$1" --name "${2:-}" ;;
  shell)        need_docker; need_env; dc exec web python manage.py shell ;;
  psql)         need_docker; need_env; dc exec db psql -U "$(env_get POSTGRES_USER)" -d "$(env_get POSTGRES_DB)" ;;
  test)         need_docker; need_env; dc exec -T web python manage.py test apps ;;
  demo)         need_docker; need_env; dc exec -T web python manage.py seed_demo ;;
  backup)       cmd_backup ;;
  backup-ftp)   cmd_backup_ftp ;;
  list)         cmd_list "$@" ;;
  restore)      cmd_restore "$@" ;;
  restore-ftp)  cmd_restore_ftp "$@" ;;
  cron)         cmd_cron "$@" ;;
  cron-remove)  cmd_cron_remove ;;
  help|-h|--help) cmd_help ;;
  *)            cmd_help; die "Comando desconocido: $command" ;;
esac
