"""Informes básicos. Solo superusuario (web, API y MCP usan estas mismas funciones)."""
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum

from apps.sales.models import HourBank, Subscription, SubscriptionEvent

from .models import LossReason, Opportunity, Stage

ZERO = Decimal(0)


def _money(value):
    return (value or ZERO).quantize(Decimal("0.01"))


def pipeline_by_stage():
    rows = []
    for stage in Stage.objects.filter(is_won=False, is_lost=False):
        agg = stage.opportunities.aggregate(n=Count("id"), mrr=Sum("amount_mrr"), services=Sum("amount_services"), one_off=Sum("amount_one_off"))
        mrr, services, one_off = _money(agg["mrr"]), _money(agg["services"]), _money(agg["one_off"])
        first_year = mrr * 12 + services + one_off
        rows.append({
            "stage": stage.name, "color": stage.color, "probability": stage.probability, "count": agg["n"],
            "mrr": mrr, "services": services, "one_off": one_off, "first_year": first_year,
            "weighted": _money(first_year * stage.probability / 100),
        })
    top = max((r["first_year"] for r in rows), default=ZERO) or Decimal(1)
    for row in rows:
        row["bar"] = int(row["first_year"] / top * 100)
    return rows


def conversion(since=None):
    closed = Opportunity.objects.filter(closed_at__isnull=False)
    if since:
        closed = closed.filter(closed_at__date__gte=since)
    won = closed.filter(stage__is_won=True).count()
    lost = closed.filter(stage__is_lost=True).count()
    total = won + lost
    by_owner = []
    for row in closed.values("owner__name", "owner__email").annotate(
        won=Count("id", filter=Q(stage__is_won=True)), lost=Count("id", filter=Q(stage__is_lost=True))
    ).order_by("-won"):
        n = row["won"] + row["lost"]
        by_owner.append({"owner": row["owner__name"] or row["owner__email"] or "Sin responsable", "won": row["won"], "lost": row["lost"],
                         "rate": round(row["won"] / n * 100) if n else 0})
    return {"won": won, "lost": lost, "rate": round(won / total * 100) if total else None, "by_owner": by_owner}


def sales_cycle_days(since=None):
    won = Opportunity.objects.filter(stage__is_won=True, closed_at__isnull=False)
    if since:
        won = won.filter(closed_at__date__gte=since)
    avg = won.annotate(d=ExpressionWrapper(F("closed_at") - F("created_at"), output_field=DurationField())).aggregate(a=Avg("d"))["a"]
    return round(avg.total_seconds() / 86400, 1) if avg else None


def monthly_forecast(months=6, today=None):
    """Previsión por mes de cierre estimado: importe del primer año ponderado por probabilidad."""
    today = today or date.today()
    buckets = OrderedDict()
    year, month = today.year, today.month
    for _ in range(months):
        buckets[(year, month)] = {"month": date(year, month, 1), "count": 0, "mrr": ZERO, "services": ZERO, "one_off": ZERO, "weighted": ZERO}
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    undated = {"count": 0, "weighted": ZERO}
    for opp in Opportunity.objects.filter(stage__is_won=False, stage__is_lost=False):
        if not opp.expected_close:
            undated["count"] += 1
            undated["weighted"] += opp.weighted_value
            continue
        # lo atrasado se cuenta en el mes actual: sigue pendiente de cerrar
        key = max((opp.expected_close.year, opp.expected_close.month), (today.year, today.month))
        if key in buckets:
            b = buckets[key]
            b["count"] += 1
            b["mrr"] += opp.amount_mrr * opp.probability / 100
            b["services"] += opp.amount_services * opp.probability / 100
            b["one_off"] += opp.amount_one_off * opp.probability / 100
            b["weighted"] += opp.weighted_value
    rows = list(buckets.values())
    top = max((r["weighted"] for r in rows), default=ZERO) or Decimal(1)
    for row in rows:
        for key in ("mrr", "services", "one_off", "weighted"):
            row[key] = _money(row[key])
        row["bar"] = int(row["weighted"] / top * 100)
    undated["weighted"] = _money(undated["weighted"])
    return {"months": rows, "undated": undated}


def loss_reasons():
    rows = [{"reason": r.name, "count": r.n} for r in LossReason.objects.annotate(n=Count("opportunity")).filter(n__gt=0).order_by("-n")]
    top = max((r["count"] for r in rows), default=1)
    for row in rows:
        row["bar"] = int(row["count"] / top * 100)
    return rows


def recurring():
    """MRR vivo y movimientos de los últimos 12 meses (altas, expansión, contracción, bajas)."""
    active = Subscription.objects.filter(status=Subscription.Status.ACTIVE).select_related("product")
    mrr = sum((s.mrr for s in active), ZERO)
    moves = {k: ZERO for k, _ in SubscriptionEvent.Kind.choices}
    since = date.today().replace(year=date.today().year - 1)
    for row in SubscriptionEvent.objects.filter(occurred_on__gte=since).values("kind").annotate(t=Sum("mrr_delta")):
        moves[row["kind"]] = _money(row["t"])
    banks = HourBank.objects.filter(closed=False)
    sold = banks.aggregate(t=Sum("hours_sold"))["t"] or ZERO
    used = banks.aggregate(t=Sum("entries__hours"))["t"] or ZERO
    return {"mrr": _money(mrr), "arr": _money(mrr * 12), "customers": active.values("company").distinct().count(),
            "moves": moves, "hours_sold": sold, "hours_used": used, "hours_left": sold - used}


def summary():
    stages = pipeline_by_stage()
    return {
        "pipeline": stages,
        "pipeline_total": {k: sum((r[k] for r in stages), ZERO) for k in ("mrr", "services", "one_off", "first_year", "weighted")},
        "pipeline_count": sum(r["count"] for r in stages),
        "conversion": conversion(),
        "cycle_days": sales_cycle_days(),
        "forecast": monthly_forecast(),
        "loss_reasons": loss_reasons(),
        "recurring": recurring(),
    }
