"""Proceso en segundo plano: recordatorios, resumen diario, reintentos de webhooks y captura de correo, cada 5 minutos."""
import time
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone


class Command(BaseCommand):
    help = "Bucle de tareas periódicas (servicio «worker» de Docker)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=int, default=300, help="Segundos entre pasadas")
        parser.add_argument("--digest-hour", type=int, default=8, help="Hora local del resumen diario (lunes a viernes)")

    def handle(self, *args, **options):
        marker = Path(settings.MEDIA_ROOT) / ".worker" / "digest"
        marker.parent.mkdir(parents=True, exist_ok=True)
        self.stdout.write("Worker en marcha")
        while True:
            close_old_connections()
            try:
                now = timezone.localtime()
                today = now.date().isoformat()
                digest = now.weekday() < 5 and now.hour >= options["digest_hour"] and (not marker.exists() or marker.read_text() != today)
                call_command("send_reminders", *(["--digest"] if digest else []))
                if digest:
                    marker.write_text(today)
                call_command("retry_webhooks")
                call_command("sync_mail")
            except Exception as exc:  # un fallo puntual (SMTP, red) no debe parar el bucle
                self.stderr.write(f"Error en la pasada: {exc}")
            time.sleep(options["interval"])
