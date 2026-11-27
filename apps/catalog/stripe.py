"""Precios del catálogo leídos de Stripe.

Stripe es la fuente de verdad de lo que se cobra: cada producto con precio recurrente en euros
tiene un precio mensual y, si acaso, otro anual, con una cuota base y un precio por usuario que
puede bajar por tramos. Aquí se vuelcan al catálogo sin duplicar: se casa por id de Stripe, luego
por código y luego por nombre; solo se crea el producto si no existe de ninguna forma.

El código sale del metadato «code» del producto en Stripe o, si no lo tiene, de su nombre
(«Plataforma - Pro» → PLATAFORMA-PRO). Un nombre «<Familia> - <Plan>» coloca además el producto
en esa familia y, si el plan se llama View, Lite, Basic, Plus o Pro, le da su nivel.
"""
import json
import re
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import BillingModel, Product, ProductFamily

TIER_LEVEL = {"VIEW": 0, "LITE": 1, "BASIC": 2, "PLUS": 3, "PRO": 4}
API = "https://api.stripe.com/v1/"


class StripeError(Exception):
    pass


def enabled():
    return bool(settings.STRIPE_SECRET_KEY)


def _get(path, params):
    url = API + path + "?" + urllib.parse.urlencode(params, doseq=True)
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        try:
            message = json.load(err).get("error", {}).get("message")
        except Exception:
            message = None
        raise StripeError(message or f"Stripe respondió {err.code}") from err
    except OSError as err:
        raise StripeError(f"No se pudo conectar con Stripe: {err}") from err


def fetch_prices():
    """Todos los precios recurrentes activos, con su producto y tramos."""
    prices, after = [], None
    while True:
        params = {"active": "true", "type": "recurring", "limit": 100, "expand[]": ["data.product", "data.tiers"]}
        if after:
            params["starting_after"] = after
        page = _get("prices", params)
        prices += page.get("data") or []
        if not page.get("has_more"):
            return prices
        after = prices[-1]["id"]


def _eur(cents):
    return None if cents is None else (Decimal(cents) / 100).quantize(Decimal("0.01"))


def catalog(prices):
    """{id de producto: {"product": {...}, "month": price, "year": price}} de los productos activos en euros."""
    found = {}
    for price in prices:
        product = price.get("product") or {}
        interval = (price.get("recurring") or {}).get("interval")
        if not (product.get("id") and (product.get("name") or "").strip() and product.get("active")
                and interval in ("month", "year") and price.get("currency") == "eur"):
            continue
        found.setdefault(product["id"], {"product": product})[interval] = price
    return found


def _code(product):
    """Código del catálogo: el metadato «code» de Stripe o el nombre en mayúsculas con guiones."""
    code = ((product.get("metadata") or {}).get("code") or "").strip()
    return (code or re.sub(r"[^A-Z0-9]+", "-", product["name"].upper()).strip("-"))[:40]


def _split(name):
    """«Plataforma - Pro» → ("Plataforma", "PRO"); sin guion no hay familia ni plan."""
    family, sep, tier = name.rpartition(" - ")
    return (family.strip(), tier.strip().upper()) if sep and family.strip() else ("", "")


def _base(price):
    """Cuota base del precio: importe fijo del primer tramo (o el unitario si no va por tramos)."""
    if not price:
        return None
    tiers = price.get("tiers") or []
    if tiers:
        return _eur(tiers[0].get("flat_amount") or tiers[0].get("unit_amount"))
    return _eur(price.get("unit_amount"))


def _user_tiers(month, year):
    rows = {}
    for interval, price in (("month", month), ("year", year)):
        for tier in (price or {}).get("tiers") or []:
            if tier.get("unit_amount"):
                rows.setdefault(tier.get("up_to"), {"up_to": tier.get("up_to")})[interval] = float(_eur(tier["unit_amount"]))
    return sorted(rows.values(), key=lambda r: (r["up_to"] is None, r["up_to"] or 0))


def _find(product):
    name = product["name"].strip()
    return (Product.objects.filter(stripe_product_id=product["id"]).first()
            or Product.objects.filter(stripe_product_id="", code__iexact=_code(product)).first()
            or Product.objects.filter(stripe_product_id="", name__iexact=name).first())


def _family(name):
    if not name:
        return None
    return ProductFamily.objects.filter(name__iexact=name).first() or ProductFamily.objects.create(name=name[:80])


@transaction.atomic
def sync(prices=None):
    """Vuelca los precios de Stripe al catálogo. Devuelve {"created": [...], "updated": [...], "same": [...]}."""
    if prices is None:
        if not enabled():
            raise StripeError("Falta STRIPE_SECRET_KEY en la configuración del servidor.")
        prices = fetch_prices()
    result = {"created": [], "updated": [], "same": []}
    now = timezone.now()
    for entry in sorted(catalog(prices).values(), key=lambda e: e["product"]["name"]):
        source = entry["product"]
        month, year = entry.get("month"), entry.get("year")
        values = {"price": _base(month), "price_annual": _base(year), "user_tiers": _user_tiers(month, year), "stripe_product_id": source["id"]}
        product = _find(source)
        if product is None:
            family, tier = _split(source["name"].strip())
            product = Product.objects.create(
                family=_family(family), code=_code(source), name=source["name"].strip()[:120],
                billing_model=BillingModel.MONTHLY, unit="mes", tier_level=TIER_LEVEL.get(tier), stripe_synced_at=now,
                description=source.get("description") or "", **values,
            )
            result["created"].append(product)
            continue
        changed = [k for k, v in values.items() if getattr(product, k) != v]
        for k in changed:
            setattr(product, k, values[k])
        product.stripe_synced_at = now
        product.save()
        result["updated" if changed else "same"].append(product)
    return result
