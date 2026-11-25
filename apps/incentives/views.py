from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import AccessLog, User, log_access

from .models import Incentive, ProductivityYear

ZERO = Decimal(0)


def overview(request):
    """Mis incentivos. Cada persona ve los suyos; quien los gestiona ve los de todo el equipo y puede filtrar por persona."""
    user = request.user
    manager = user.can_manage_incentives
    people = User.objects.filter(is_active=True).order_by("name") if manager else None
    person = None
    if manager and request.GET.get("persona", "").isdigit():
        person = people.filter(pk=request.GET["persona"]).first()
    if not manager:
        person = user
    incentives = Incentive.objects.select_related("user", "company", "opportunity")
    if person:
        incentives = incentives.filter(user=person)
    incentives = list(incentives)
    live = [i for i in incentives if i.status == Incentive.Status.APPROVED]
    totals = {
        "expected": sum((i.total for i in live), ZERO), "earned": sum((i.earned for i in live), ZERO),
        "paid": sum((i.paid for i in live), ZERO), "due": sum((i.due for i in live), ZERO),
        "proposed": sum((i.total for i in incentives if i.status == Incentive.Status.PROPOSED), ZERO),
        "n_proposed": sum(1 for i in incentives if i.status == Incentive.Status.PROPOSED),
    }
    years = []
    for year in ProductivityYear.objects.all() if manager else ProductivityYear.objects.filter(published=True):
        shares = year.shares()
        if person:
            shares = [s for s in shares if s.user_id == person.pk]
        years.append({"year": year, "scores": shares, "n_people": year.scores.filter(eligible=True).count()})
    programs = [(label, [i for i in incentives if i.program == value], value) for value, label in Incentive.Program.choices]
    return render(request, "incentives/overview.html", {
        "manager": manager, "people": people, "person": person, "programs": programs, "totals": totals, "years": years,
        "own_view": person is not None and person.pk == user.pk,
    })


@require_POST
def decide(request, pk):
    """Validar o descartar un incentivo propuesto."""
    if not request.user.can_manage_incentives:
        raise PermissionDenied
    incentive = get_object_or_404(Incentive, pk=pk)
    if request.POST.get("status") in Incentive.Status.values:
        incentive.status, incentive.decided_by, incentive.decided_at = request.POST["status"], request.user, timezone.now()
        incentive.save(update_fields=["status", "decided_by", "decided_at"])
        log_access(request, AccessLog.Action.UPDATE, incentive)
    return redirect(request.POST.get("next") or "incentives")
