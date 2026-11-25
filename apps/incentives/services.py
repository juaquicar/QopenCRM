"""Propuesta automática de incentivos de ventas cuando se gana una oportunidad con un cliente nuevo."""
from .models import Incentive

EXCLUDED_ROLES = {"ceo"}  # el programa de ventas no aplica a CEO y CTO


def propose_for(opportunity, was_customer):
    """Deja propuestos (pendientes de validar) los incentivos de ventas que podrían corresponder al responsable."""
    owner = opportunity.owner
    if owner is None or was_customer or owner.is_superuser or (owner.role_id and owner.role.code in EXCLUDED_ROLES):
        return []
    concept = f"{opportunity.company.name} · {opportunity.title}"[:200]
    common = {"user": owner, "opportunity": opportunity, "company": opportunity.company}
    note = "Propuesto automáticamente al ganar la oportunidad. Revisar requisitos del programa: cliente nuevo, captación propia e implicación hasta el cobro."
    created = []
    project = opportunity.amount_services + opportunity.amount_one_off
    if project > 0:
        incentive, new = Incentive.objects.get_or_create(program=Incentive.Program.SALES_NEW, **common, defaults={
            "concept": concept, "revenue": project, "notes": note + " Faltan los costes directos."})
        created += [incentive] if new else []
    if opportunity.amount_mrr > 0:
        quota = opportunity.amount_mrr
        incentive, new = Incentive.objects.get_or_create(program=Incentive.Program.SALES_QUOTA, **common, defaults={
            "concept": concept, "quota_1": quota, "quota_2": quota, "quota_3": quota, "quota_4": quota,
            "notes": note + " Cuotas estimadas con el recurrente de la oportunidad."})
        created += [incentive] if new else []
    return created
