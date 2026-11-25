"""Recordatorios por correo. Pensado para cron o el servicio «worker»: cada 15 minutos.

- Aviso de cada tarea cuando llega su hora (una sola vez).
- Con --digest: resumen diario con lo vencido, lo de hoy y las oportunidades sin próxima acción.
"""
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import User
from apps.crm.models import Opportunity, Task
from apps.crm.templatetags.crm import due


class Command(BaseCommand):
    help = "Envía recordatorios de tareas por correo."

    def add_arguments(self, parser):
        parser.add_argument("--digest", action="store_true", help="Enviar el resumen diario")
        parser.add_argument("--ahead", type=int, default=15, help="Minutos de antelación")

    def handle(self, *args, **options):
        now = timezone.now()
        sent = 0
        pending = Task.objects.filter(
            done_at__isnull=True, reminded_at__isnull=True, due_at__lte=now + timedelta(minutes=options["ahead"]),
            due_at__gte=now - timedelta(days=2), assignee__is_active=True, assignee__task_emails=True,
        ).select_related("assignee", "opportunity", "company")
        for task in pending:
            where = task.opportunity or task.company
            path = f"/oportunidades/{task.opportunity_id}/" if task.opportunity_id else (f"/cuentas/{task.company_id}/" if task.company_id else "/tareas/")
            send_mail(
                f"Recordatorio: {task.title}",
                f"{task.title}\nCuándo: {due(task.due_at)}\n" + (f"Sobre: {where}\n" if where else "") + (f"\n{task.notes}\n" if task.notes else "")
                + f"\n{settings.SITE_URL}{path}",
                None, [task.assignee.email],
            )
            Task.objects.filter(pk=task.pk).update(reminded_at=now)
            sent += 1
        if options["digest"]:
            start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
            for user in User.objects.filter(is_active=True, task_emails=True):
                mine = Task.objects.filter(assignee=user, done_at__isnull=True)
                overdue = list(mine.filter(due_at__lt=start))
                today = list(mine.filter(due_at__gte=start, due_at__lt=start + timedelta(days=1)))
                cold = list(Opportunity.objects.filter(Q(owner=user), stage__is_won=False, stage__is_lost=False, next_action_at__isnull=True))
                if not (overdue or today or cold):
                    continue
                lines = [f"Buenos días, {user.name or ''}".strip() + ":", ""]
                for title, items in (("VENCIDAS", overdue), ("HOY", today)):
                    if items:
                        lines += [title] + [f"  · {t.title} ({due(t.due_at)})" + (f" — {t.opportunity or t.company}" if (t.opportunity or t.company) else "") for t in items] + [""]
                if cold:
                    lines += ["OPORTUNIDADES SIN PRÓXIMA ACCIÓN"] + [f"  · {o.title} — {o.company}" for o in cold] + [""]
                lines.append(settings.SITE_URL)
                send_mail(f"Tu día en QopenCRM: {len(overdue) + len(today)} tareas, {len(cold)} sin próximo paso", "\n".join(lines), None, [user.email])
                sent += 1
        self.stdout.write(f"{sent} correos enviados")
