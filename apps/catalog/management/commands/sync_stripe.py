"""Actualiza los precios del catálogo desde Stripe (mensual, anual y por usuario)."""
from django.core.management.base import BaseCommand, CommandError

from apps.catalog import stripe


class Command(BaseCommand):
    help = "Trae de Stripe los precios mensual y anual de los productos con precio recurrente en euros."

    def handle(self, *args, **options):
        try:
            result = stripe.sync()
        except stripe.StripeError as err:
            raise CommandError(str(err)) from err
        for label, key in (("Nuevo", "created"), ("Actualizado", "updated"), ("Sin cambios", "same")):
            for p in result[key]:
                self.stdout.write(f"{label}: {p.code} · {p.price} €/mes · {p.price_annual} €/año")
        self.stdout.write(f"{len(result['created'])} creados, {len(result['updated'])} actualizados, {len(result['same'])} sin cambios.")
