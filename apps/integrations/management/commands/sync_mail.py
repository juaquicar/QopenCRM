from django.conf import settings
from django.core.management.base import BaseCommand

from apps.integrations.mail import sync
from apps.integrations.models import MailboxState


class Command(BaseCommand):
    help = "Captura correos y reuniones del buzón compartido y los registra en el historial de cada cuenta."

    def add_arguments(self, parser):
        parser.add_argument("--rescan", action="store_true",
                            help="Vuelve a leer los últimos MAIL_CAPTURE_SINCE_DAYS días (útil tras importar contactos; no duplica)")

    def handle(self, *args, **options):
        if options["rescan"]:
            MailboxState.objects.update(last_uid=0)
        if not settings.MAIL_CAPTURE_HOST:
            self.stdout.write("Captura de correo sin configurar (MAIL_CAPTURE_HOST vacío).")
            return
        seen, stored = sync(self.stdout.write)
        self.stdout.write(f"{seen} mensajes leídos, {stored} registrados")
