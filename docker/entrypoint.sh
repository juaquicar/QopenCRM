#!/bin/sh
# Arranque del contenedor: espera a la base de datos y deja el esquema al día.
set -e

python - <<'PY'
import os, sys, time
import psycopg
for attempt in range(60):
    try:
        psycopg.connect(host=os.environ["POSTGRES_HOST"], port=os.environ.get("POSTGRES_PORT", "5432"),
                        dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                        password=os.environ["POSTGRES_PASSWORD"], connect_timeout=3).close()
        break
    except psycopg.OperationalError:
        time.sleep(1)
else:
    sys.exit("La base de datos no responde")
PY

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
    python manage.py migrate --noinput
    python manage.py seed
fi

exec "$@"
