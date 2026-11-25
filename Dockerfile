FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"

# Pango y fuentes: los necesita WeasyPrint para generar los PDF de presupuestos
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 fonts-dejavu-core fontconfig \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY docker/fonts/ /usr/local/share/fonts/qopencrm/
RUN fc-cache -f

COPY . .
RUN DJANGO_SECRET_KEY=build DJANGO_DEBUG=0 python manage.py collectstatic --noinput \
    && useradd --system --uid 1000 --home /app crm \
    && mkdir -p /app/media && chown -R crm:crm /app/media

USER crm
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
