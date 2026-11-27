"""Pruebas de extremo a extremo del flujo comercial."""
import io
import re
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import AccessLog, Role, User
from apps.catalog.models import Product, ProductFamily
from apps.crm import reports
from apps.crm.models import Activity, Company, Contact, LossReason, Opportunity, OpportunityLine, Stage, Task
from apps.integrations.models import ApiKey
from apps.sales.models import HourBank, Quote, Subscription, SubscriptionEvent


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)
        cls.boss = User.objects.create_superuser("jefe@example.com", name="Jefa Suprema")
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))
        cls.luis = User.objects.create_user("luis@example.com", name="Luis Ventas", role=Role.objects.get(code="comercial"))
        family = ProductFamily.objects.create(name="Atlas Telco")
        cls.pro = Product.objects.create(family=family, code="TELCO-PRO", name="Telco Pro", billing_model="monthly", unit="mes", price=500, price_annual=5000, tier_level=4)
        cls.basic = Product.objects.create(family=family, code="TELCO-BASIC", name="Telco Basic", billing_model="monthly", unit="mes", price=200, tier_level=2)
        cls.seat = Product.objects.create(family=family, code="USER", name="Usuario extra", billing_model="monthly", unit="usuario/mes", price=20, is_addon=True)
        cls.training = Product.objects.create(code="FORM", name="Formación", billing_model="hourly", unit="hora", price=60)
        cls.setup = Product.objects.create(code="SETUP", name="Puesta en marcha", billing_model="one_off", unit="proyecto", price=1500)
        cls.elektra = Product.objects.create(code="ELEKTRA", name="Atlas Elektra", billing_model="monthly", unit="mes", price=None)
        cls.stages = {s.name: s for s in Stage.objects.all()}
        cls.acme = Company.objects.create(name="Fibra Acme, S.L.", tax_id="B-1234 5678", website="https://www.acme.es/", owner=cls.ana)
        cls.other = Company.objects.create(name="Redes Luis SA", owner=cls.luis)

    def make_opp(self, owner=None, company=None, **kwargs):
        return Opportunity.objects.create(title="Despliegue FTTH", company=company or self.acme, stage=self.stages["Nuevo lead"], owner=owner or self.ana, **kwargs)

    def mixed_lines(self, opp):
        OpportunityLine.objects.create(opportunity=opp, product=self.pro, billing_model="monthly", quantity=1, unit_price=500)
        OpportunityLine.objects.create(opportunity=opp, product=self.seat, billing_model="monthly", quantity=5, unit_price=20)
        OpportunityLine.objects.create(opportunity=opp, product=self.training, billing_model="hourly", quantity=20, unit_price=60)
        OpportunityLine.objects.create(opportunity=opp, product=self.setup, billing_model="one_off", quantity=1, unit_price=1500, discount=10)
        opp.refresh_from_db()


class LoginTests(Base):
    def test_code_login(self):
        self.assertRedirects(self.client.get("/"), "/entrar/?next=/")
        self.client.post("/entrar/", {"email": "ANA@example.com "})
        self.assertEqual(len(mail.outbox), 1)
        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
        self.assertTrue(mail.outbox[0].subject.startswith("[QopenCRM] ") and code in mail.outbox[0].subject)
        self.assertContains(self.client.post("/entrar/codigo/", {"code": "000000" if code != "000000" else "111111"}), "incorrecto")
        self.assertRedirects(self.client.post("/entrar/codigo/", {"code": code}), "/")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertTrue(AccessLog.objects.filter(user=self.ana, action="login").exists())
        # volver a entrar enseguida no manda otro correo: el mismo código vale mientras no caduque (otro dispositivo, por ejemplo)
        self.client.post("/salir/")
        self.client.post("/entrar/", {"email": "ana@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertRedirects(self.client.post("/entrar/codigo/", {"code": code}), "/")
        # pasados los 10 minutos caduca y ya se puede pedir otro
        from apps.accounts.models import LoginCode
        LoginCode.objects.update(created_at=timezone.now() - timedelta(minutes=11), expires_at=timezone.now() - timedelta(minutes=1))
        self.client.post("/salir/")
        self.client.post("/entrar/", {"email": "ana@example.com"})
        self.assertEqual(len(mail.outbox), 2)
        self.assertContains(self.client.post("/entrar/codigo/", {"code": code if code != re.search(r"\b(\d{6})\b", mail.outbox[1].body).group(1) else "x"}), "incorrecto")

    def test_unknown_email_reveals_nothing(self):
        response = self.client.post("/entrar/", {"email": "nadie@example.com"})
        self.assertRedirects(response, "/entrar/codigo/")
        self.assertEqual(len(mail.outbox), 0)

    def test_attempt_limit(self):
        self.client.post("/entrar/", {"email": "ana@example.com"})
        code = re.search(r"\b(\d{6})\b", mail.outbox[0].body).group(1)
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.client.post("/entrar/codigo/", {"code": wrong})
        self.assertContains(self.client.post("/entrar/codigo/", {"code": code}), "incorrecto")


    def test_code_requests_are_capped_per_user(self):
        from django.test import Client
        for _ in range(8):  # ocho navegadores distintos pidiendo código para la misma persona
            Client().post("/entrar/", {"email": "ana@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # un correo cada 10 minutos por persona, lo pida quien lo pida


class CommercialFlowTests(Base):
    def test_amounts_split_by_billing_model(self):
        opp = self.make_opp()
        self.mixed_lines(opp)
        self.assertEqual(opp.amount_mrr, Decimal("600.00"))
        self.assertEqual(opp.amount_services, Decimal("1200.00"))
        self.assertEqual(opp.amount_one_off, Decimal("1350.00"))
        self.assertEqual(opp.first_year_value, Decimal("9750.00"))
        # anual: el MRR es la doceava parte; sin precio no suma
        OpportunityLine.objects.create(opportunity=opp, product=self.basic, billing_model="monthly", period="annual", quantity=1, unit_price=2400)
        OpportunityLine.objects.create(opportunity=opp, product=self.elektra, billing_model="monthly", quantity=1, unit_price=None)
        opp.refresh_from_db()
        self.assertEqual(opp.amount_mrr, Decimal("800.00"))

    def test_next_action_discipline(self):
        opp = self.make_opp()
        self.assertIsNone(opp.next_action_at)
        later = Task.objects.create(title="Enviar propuesta", due_at=timezone.now() + timedelta(days=3), assignee=self.ana, opportunity=opp)
        first = Task.objects.create(title="Llamar", due_at=timezone.now() + timedelta(days=1), assignee=self.ana, opportunity=opp)
        opp.refresh_from_db()
        self.assertEqual(opp.next_action_at, first.due_at)
        self.client.force_login(self.ana)
        self.client.post(f"/tareas/{first.pk}/hecha/")
        opp.refresh_from_db()
        self.assertEqual(opp.next_action_at, later.due_at)
        # al cerrar la última, se pide la siguiente
        response = self.client.post(f"/tareas/{later.pk}/hecha/")
        self.assertIn("openModal", response.headers["HX-Trigger"])
        opp.refresh_from_db()
        self.assertIsNone(opp.next_action_at)
        self.assertContains(self.client.get("/"), "Sin próxima acción")
        self.client.post("/tareas/rapida/", {"title": "Volver a llamar", "quick": "3", "opportunity": opp.pk})
        opp.refresh_from_db()
        self.assertIsNotNone(opp.next_action_at)
        self.assertLess(timezone.localtime(opp.next_action_at).weekday(), 5)

    def test_win_creates_subscriptions_and_hour_banks(self):
        opp = self.make_opp()
        self.mixed_lines(opp)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(f"/oportunidades/{opp.pk}/mover/", {"stage": self.stages["Ganada"].pk}).status_code, 204)
        opp.refresh_from_db()
        self.assertTrue(opp.stage.is_won)
        self.assertIsNotNone(opp.closed_at)
        subs = Subscription.objects.filter(company=self.acme)
        self.assertEqual(subs.count(), 2)
        self.assertEqual(sum(s.mrr for s in subs), Decimal("600.00"))
        self.assertEqual(SubscriptionEvent.objects.filter(kind="new").count(), 2)
        bank = HourBank.objects.get(company=self.acme)
        self.assertEqual(bank.hours_sold, 20)
        self.acme.refresh_from_db()
        self.assertEqual(self.acme.kind, "customer")
        self.assertTrue(Activity.objects.filter(opportunity=opp, kind="stage").exists())
        # cambio de plan Pro → Basic queda como contracción
        sub = subs.get(product=self.pro)
        event = sub.change(self.boss, product=self.basic, unit_price=Decimal("200"))
        self.assertEqual((event.kind, event.mrr_delta), ("downgrade", Decimal("-300.00")))
        self.assertEqual(sub.cancel(self.boss).mrr_delta, Decimal("-200.00"))
        # horas vendidas frente a consumidas
        bank.entries.create(hours=Decimal("7.5"), description="Formación inicial", user=self.ana)
        self.assertEqual(bank.hours_left, Decimal("12.5"))

    def test_losing_requires_reason(self):
        opp = self.make_opp()
        self.client.force_login(self.ana)
        lost = self.stages["Perdida"]
        response = self.client.post(f"/oportunidades/{opp.pk}/mover/", {"stage": lost.pk})
        self.assertIn("perder", response.headers["HX-Trigger"])
        opp.refresh_from_db()
        self.assertTrue(opp.stage.is_open)
        reason = LossReason.objects.get(name="Precio")
        self.client.post(f"/oportunidades/{opp.pk}/perder/{lost.pk}/", {"loss_reason": reason.pk, "loss_notes": "Un 20 % más caro"})
        opp.refresh_from_db()
        self.assertEqual((opp.stage, opp.loss_reason), (lost, reason))

    def test_versioned_quotes_with_pdf(self):
        opp = self.make_opp()
        self.mixed_lines(opp)
        self.client.force_login(self.ana)
        with self.settings(MEDIA_ROOT=self.enterContext(__import__("tempfile").TemporaryDirectory())):
            self.client.post(f"/oportunidades/{opp.pk}/presupuesto/")
            OpportunityLine.objects.filter(opportunity=opp, product=self.seat).first().delete()
            self.client.post(f"/oportunidades/{opp.pk}/presupuesto/")
            first, second = Quote.objects.order_by("version")
            self.assertEqual((first.number, first.version, second.version), (second.number, 1, 2))
            self.assertEqual((first.total_mrr, second.total_mrr), (Decimal("600.00"), Decimal("500.00")))
            self.assertEqual(len(first.lines), 4)  # la versión 1 no cambia al editar la oportunidad
            response = self.client.get(f"/presupuestos/{second.pk}/pdf/")
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
            # otro comercial no puede abrir el PDF
            self.client.force_login(self.luis)
            self.assertEqual(self.client.get(f"/presupuestos/{second.pk}/pdf/").status_code, 404)

    def test_timeline_capture(self):
        self.client.force_login(self.ana)
        response = self.client.post("/actividad/", {"company": self.acme.pk, "kind": "call", "body": "Quieren demo el martes"})
        self.assertContains(response, "Quieren demo el martes")
        self.assertEqual(self.acme.activities.get().kind, "call")
        self.assertEqual(self.client.post("/actividad/", {"company": self.other.pk, "body": "x"}).status_code, 404)


class PermissionTests(Base):
    def test_owner_scope(self):
        mine, theirs = self.make_opp(), self.make_opp(owner=self.luis, company=self.other)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(f"/oportunidades/{mine.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/oportunidades/{theirs.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/cuentas/{self.other.pk}/").status_code, 404)
        self.assertNotContains(self.client.get("/cuentas/"), "Redes Luis")
        self.assertEqual(self.client.post(f"/oportunidades/{theirs.pk}/mover/", {"stage": self.stages["Contactado"].pk}).status_code, 404)
        # asignada: la ve y la edita
        theirs.assignees.add(self.ana)
        self.assertEqual(self.client.get(f"/oportunidades/{theirs.pk}/").status_code, 200)
        self.assertEqual(self.client.post(f"/oportunidades/{theirs.pk}/mover/", {"stage": self.stages["Contactado"].pk}).status_code, 204)

    def test_role_gates(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get("/informes/").status_code, 403)
        self.assertEqual(self.client.get("/ajustes/exportar/").status_code, 403)
        self.assertEqual(self.client.get("/ajustes/importar/").status_code, 403)
        self.assertEqual(self.client.get("/m/product/").status_code, 403)
        self.assertEqual(self.client.get("/m/user/").status_code, 403)
        self.assertEqual(self.client.post(f"/m/company/{self.acme.pk}/borrar/").status_code, 403)
        self.assertNotContains(self.client.get("/ajustes/"), "Registro de accesos")
        self.client.force_login(self.boss)
        for url in ("/", "/pipeline/", "/pipeline/?view=list", "/cuentas/", "/contactos/", "/tareas/", "/productos/", "/suscripciones/", "/informes/", "/ajustes/", "/ajustes/importar/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.post(f"/m/company/{self.acme.pk}/borrar/").status_code, 204)

    def test_superuser_configures_roles(self):
        role = Role.objects.get(code="comercial")
        self.client.force_login(self.boss)
        response = self.client.post(f"/m/role/{role.pk}/", {"name": "Comercial", "view_scope": "all", "edit_scope": "own", "can_use_api": "on"})
        self.assertEqual(response.status_code, 204)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(f"/cuentas/{self.other.pk}/").status_code, 200)
        self.assertEqual(self.client.post(f"/m/company/{self.other.pk}/", {"name": "Hackeada", "kind": "prospect"}).status_code, 403)


class ModalTests(Base):
    def test_create_through_dialogs(self):
        self.client.force_login(self.ana)
        response = self.client.post("/m/company/", {"name": "Nueva Telecom", "kind": "prospect", "country": "España", "owner": self.ana.pk})
        company = Company.objects.get(name="Nueva Telecom")
        self.assertEqual(response.headers["HX-Redirect"], f"/cuentas/{company.pk}/")
        response = self.client.post(f"/m/opportunity/?company={company.pk}", {
            "title": "Piloto", "company": company.pk, "stage": self.stages["Nuevo lead"].pk, "owner": self.ana.pk, "priority": 3})
        opp = Opportunity.objects.get(title="Piloto")
        self.assertEqual(response.headers["HX-Redirect"], f"/oportunidades/{opp.pk}/")
        self.assertContains(self.client.get(f"/m/line/?opportunity={opp.pk}"), "line-prices")
        self.client.post(f"/m/line/?opportunity={opp.pk}", {"product": self.training.pk, "quantity": "10", "unit_price": "60", "discount": "0", "period": "monthly"})
        opp.refresh_from_db()
        self.assertEqual(opp.amount_services, Decimal("600.00"))
        self.assertContains(self.client.get(f"/oportunidades/{opp.pk}/"), "Formación")
        self.assertContains(self.client.get("/pipeline/?priority=3"), "Piloto")
        self.assertNotContains(self.client.get("/pipeline/?priority=1"), "Piloto")
        self.assertContains(self.client.get(f"/pipeline/?product={self.training.pk}&action=none"), "Piloto")
        self.assertNotContains(self.client.get(f"/pipeline/?product={self.pro.pk}"), "Piloto")
        # un formulario inválido vuelve al diálogo con el error
        self.assertContains(self.client.post("/m/task/", {"title": ""}), "requerido")


class DataTests(Base):
    CSV = ("Empresa;CIF;Web;Nombre;Apellidos;Email;Cargo;Base legal\n"
           "FIBRA ACME SL;;;Marta;López;marta@acme.es;CTO;Contrato\n"
           "Operador Nuevo S.A.;A11111111;nuevo.es;Pepe;Ruiz;pepe@nuevo.es;CEO;\n"
           "Operador Nuevo;;;Pepe;Ruiz;pepe@nuevo.es;;\n").encode("cp1252")

    def test_duplicate_detection(self):
        twin = Company.objects.create(name="Fibra ACME")
        by_domain = Company.objects.create(name="Acme Telecomunicaciones", email="info@acme.es")
        self.assertEqual(set(self.acme.duplicates()), {twin, by_domain})
        self.assertEqual(self.acme.tax_id, "B12345678")

    def test_csv_import_dedupes(self):
        self.client.force_login(self.boss)
        preview = self.client.post("/ajustes/importar/", {"file": SimpleUploadedFile("c.csv", self.CSV), "mode": "preview"})
        self.assertContains(preview, "no se ha guardado nada")
        self.assertFalse(Contact.objects.exists())
        self.client.post("/ajustes/importar/", {"file": SimpleUploadedFile("c.csv", self.CSV), "mode": "import"})
        self.assertEqual(Company.objects.count(), 3)  # Acme ya existía; Operador Nuevo solo una vez
        self.assertEqual(Contact.objects.count(), 2)
        marta = Contact.objects.get(email="marta@acme.es")
        self.assertEqual((marta.company, marta.last_name, marta.legal_basis), (self.acme, "López", "contract"))
        self.client.post("/ajustes/importar/", {"file": SimpleUploadedFile("c.csv", self.CSV), "mode": "import"})
        self.assertEqual((Company.objects.count(), Contact.objects.count()), (3, 2))

    def test_merge(self):
        twin = Company.objects.create(name="Fibra ACME")
        Contact.objects.create(company=twin, first_name="Eva")
        self.client.force_login(self.boss)
        self.client.post(f"/cuentas/{self.acme.pk}/fusionar/{twin.pk}/")
        self.assertFalse(Company.objects.filter(pk=twin.pk).exists())
        self.assertEqual(self.acme.contacts.get().first_name, "Eva")

    def test_gdpr_erasure(self):
        contact = Contact.objects.create(company=self.acme, first_name="Marta", last_name="López", email="marta@acme.es", mobile="600111222")
        Activity.objects.create(company=self.acme, contact=contact, kind="email", subject="Oferta", body="Hola Marta")
        self.client.force_login(self.boss)
        self.client.post(f"/contactos/{contact.pk}/suprimir/")
        contact.refresh_from_db()
        self.assertEqual((contact.email, contact.mobile, contact.first_name), ("", "", "Contacto"))
        self.assertIsNotNone(contact.erased_at)
        self.assertEqual(self.acme.activities.get().body, "")
        self.assertTrue(AccessLog.objects.filter(action="erase", user=self.boss).exists())

    def test_full_export(self):
        self.make_opp()
        self.client.force_login(self.boss)
        archive = zipfile.ZipFile(io.BytesIO(self.client.get("/ajustes/exportar/").content))
        self.assertIn("company.csv", archive.namelist())
        self.assertIn("Despliegue FTTH", archive.read("opportunity.csv").decode())
        self.assertTrue(AccessLog.objects.filter(action="export").exists())

    def test_reports(self):
        open_opp = self.make_opp(expected_close=timezone.localdate())
        self.mixed_lines(open_opp)
        won = self.make_opp()
        won.move_to(self.stages["Ganada"])
        Opportunity.objects.filter(pk=won.pk).update(created_at=won.closed_at - timedelta(days=10))
        self.make_opp().move_to(self.stages["Perdida"], loss_reason=LossReason.objects.first())
        data = reports.summary()
        self.assertEqual(data["pipeline_total"]["mrr"], Decimal("600.00"))
        self.assertEqual(data["conversion"]["rate"], 50)
        self.assertEqual(data["cycle_days"], 10.0)
        self.assertEqual(data["forecast"]["months"][0]["count"], 1)
        self.assertEqual(data["loss_reasons"][0]["count"], 1)

    def test_reminders(self):
        opp = self.make_opp()
        Task.objects.create(title="Llamar ya", due_at=timezone.now() + timedelta(minutes=5), assignee=self.ana, opportunity=opp)
        Task.objects.create(title="Más adelante", due_at=timezone.now() + timedelta(days=2), assignee=self.ana)
        call_command("send_reminders", verbosity=0)
        call_command("send_reminders", verbosity=0)  # no repite
        self.assertEqual([m.subject for m in mail.outbox], ["[QopenCRM] Recordatorio: Llamar ya · Fibra Acme, S.L. · Despliegue FTTH"])
        self.assertEqual(mail.outbox[0].to, ["ana@example.com"])
        call_command("send_reminders", "--digest", verbosity=0)
        self.assertTrue(any("Tu día" in m.subject for m in mail.outbox))


class ApiTests(Base):
    def setUp(self):
        _, self.key = ApiKey.issue(self.ana, "ERP")
        self.auth = {"HTTP_AUTHORIZATION": f"Bearer {self.key}"}

    def test_auth_required(self):
        self.assertIn(self.client.get("/api/v1/companies/").status_code, (401, 403))
        self.assertEqual(self.client.get("/api/v1/companies/", HTTP_AUTHORIZATION="Bearer qcrm_falsa").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/me/", **self.auth).json()["email"], "ana@example.com")

    def test_scope_and_flow(self):
        names = [c["name"] for c in self.client.get("/api/v1/companies/", **self.auth).json()["results"]]
        self.assertEqual(names, ["Fibra Acme, S.L."])
        self.assertEqual(self.client.get("/api/v1/reports/", **self.auth).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/export/", **self.auth).status_code, 403)
        body = {"title": "Vía API", "company": self.acme.pk, "stage": self.stages["Nuevo lead"].pk}
        self.assertEqual(self.client.post("/api/v1/opportunities/", {**body, "company": self.other.pk}, content_type="application/json", **self.auth).status_code, 403)
        opp_id = self.client.post("/api/v1/opportunities/", body, content_type="application/json", **self.auth).json()["id"]
        self.assertEqual(Opportunity.objects.get(pk=opp_id).owner, self.ana)
        line = self.client.post("/api/v1/opportunity-lines/", {"opportunity": opp_id, "product": self.pro.pk, "quantity": 2}, content_type="application/json", **self.auth)
        self.assertEqual(line.json()["total"], "1000.00")
        lost = self.client.post(f"/api/v1/opportunities/{opp_id}/move/", {"stage": self.stages["Perdida"].pk}, content_type="application/json", **self.auth)
        self.assertEqual(lost.status_code, 400)
        won = self.client.post(f"/api/v1/opportunities/{opp_id}/move/", {"stage": self.stages["Ganada"].pk}, content_type="application/json", **self.auth)
        self.assertEqual(won.json()["amount_mrr"], "1000.00")
        sub = self.client.get("/api/v1/subscriptions/", **self.auth).json()["results"][0]
        self.assertEqual(sub["mrr"], "1000.00")
        changed = self.client.post(f"/api/v1/subscriptions/{sub['id']}/change/", {"quantity": "3"}, content_type="application/json", **self.auth)
        self.assertEqual(changed.json().get("mrr"), "1500.00", changed.content)
        self.assertEqual(self.client.delete(f"/api/v1/opportunities/{opp_id}/", **self.auth).status_code, 403)
        boss_key = ApiKey.issue(self.boss, "BI")[1]
        self.assertEqual(self.client.get("/api/v1/reports/", HTTP_AUTHORIZATION=f"Bearer {boss_key}").status_code, 200)
        self.assertEqual(self.client.get("/api/schema/", HTTP_AUTHORIZATION=f"Bearer {boss_key}").status_code, 200)

    def test_revoked_key(self):
        ApiKey.objects.update(revoked_at=timezone.now())
        self.assertEqual(self.client.get("/api/v1/me/", **self.auth).status_code, 401)


class ManualTests(Base):
    def test_manual_adapts_to_role(self):
        self.client.force_login(self.ana)
        page = self.client.get("/manual/")
        self.assertContains(page, "Solo lo tuyo")
        self.assertNotContains(page, 'id="informes"')
        self.assertNotContains(page, "Importar desde CSV")
        self.client.force_login(self.boss)
        page = self.client.get("/manual/")
        self.assertContains(page, 'id="informes"')
        self.assertContains(page, "Importar desde CSV")

    def test_only_superuser_reads_other_roles(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get("/manual/ceo/").status_code, 403)
        self.assertEqual(self.client.get(f"/manual/{self.ana.role.code}/").status_code, 200)
        self.client.force_login(self.boss)
        for code in ("ceo", "director", "responsable-comercial", "comercial", "superusuario"):
            self.assertEqual(self.client.get(f"/manual/{code}/").status_code, 200, code)

    def test_manual_screenshots_exist(self):
        import re
        from pathlib import Path

        from django.conf import settings

        self.client.force_login(self.boss)
        for code in ("comercial", "director", "ceo", "superusuario"):
            html = self.client.get(f"/manual/{code}/").content.decode()
            for name in set(re.findall(r"img/manual/([\w-]+)\.webp", html)):
                self.assertTrue((Path(settings.BASE_DIR) / "static/img/manual" / f"{name}.webp").exists(), name)


class QuoteSectionTests(Base):
    """Sección Presupuestos: listar, crear, ver, editar (versiona), enviar; no se borra."""

    def setUp(self):
        self.enterContext(self.settings(MEDIA_ROOT=self.enterContext(__import__("tempfile").TemporaryDirectory())))
        self.opp = self.make_opp()
        self.client.force_login(self.ana)

    def form(self, **changes):
        data = {
            "number": "P26 000002", "title": "Módulo de inventario", "bill_to": "ACME TELECOM, S.A.\nA00000000", "issued_on": "2026-01-08",
            "valid_until": "2026-02-08", "tax_rate": "21", "terms": "> Incluye:\n   * Un usuario", "intro": "",
            "line_code": ["", ""], "line_unit": ["", ""],
            "line_description": ["CAPEX: Módulo de inventario\n- Campos Personales", "OPEX:\ncuota recurrente anual"],
            "line_billing_model": ["one_off", "monthly"], "line_period": ["monthly", "annual"],
            "line_quantity": ["1", "1"], "line_unit_price": ["2.500,00", "6648"], "line_discount": ["0", "15"],
        }
        data.update(changes)
        return data

    def test_create_matches_reference_totals(self):
        response = self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form())
        quote = Quote.objects.get()
        self.assertRedirects(response, f"/presupuestos/{quote.pk}/")
        self.assertEqual((quote.number, quote.version, quote.title), ("P26 000002", 1, "Módulo de inventario"))
        self.assertEqual((quote.base, quote.tax, quote.total), (Decimal("8150.80"), Decimal("1711.67"), Decimal("9862.47")))
        self.assertEqual((quote.total_one_off, quote.total_mrr), (Decimal("2500.00"), Decimal("470.90")))
        self.assertTrue(quote.pdf.read().startswith(b"%PDF"))
        self.assertContains(self.client.get("/presupuestos/"), "P26 000002")
        self.assertContains(self.client.get(f"/presupuestos/{quote.pk}/"), "Módulo de inventario")

    def test_edit_creates_new_version_and_keeps_the_old_one(self):
        self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form())
        first = Quote.objects.get()
        self.client.post(f"/presupuestos/{first.pk}/editar/", self.form(number="OTRO", line_unit_price=["3000", "6648"]))
        first.refresh_from_db()
        second = Quote.objects.get(version=2)
        self.assertEqual((second.number, first.base, second.base), ("P26 000002", Decimal("8150.80"), Decimal("8650.80")))
        listing = self.client.get("/presupuestos/").content.decode()
        self.assertIn(f"/presupuestos/{second.pk}/", listing)
        self.assertNotIn(f'data-href="/presupuestos/{first.pk}/"', listing)  # la lista enseña solo la última versión
        self.assertContains(self.client.get(f"/presupuestos/{first.pk}/"), "Versión antigua")

    def test_numbering_follows_the_last_one(self):
        self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form(number="P26 000137"))
        from datetime import date

        from apps.sales import services
        self.assertEqual(services.next_number(date(2026, 3, 1)), "P26 000138")
        self.assertEqual(services.next_number(date(2027, 1, 2)), "P27 000001")
        other = self.make_opp(company=self.other, owner=self.ana)
        response = self.client.post(f"/presupuestos/nuevo/?opportunity={other.pk}", self.form(number="P26 000137"))
        self.assertContains(response, "Ya existe un presupuesto con el número")
        self.assertEqual(Quote.objects.count(), 1)

    def test_invalid_lines_are_rejected_without_saving(self):
        response = self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form(line_unit_price=["abc", "1"]))
        self.assertContains(response, "no es un número")
        response = self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form(line_description=["", ""]))
        self.assertContains(response, "al menos una línea")
        self.assertFalse(Quote.objects.exists())

    def test_quotes_cannot_be_deleted(self):
        self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form())
        quote = Quote.objects.get()
        self.client.force_login(self.boss)
        self.assertEqual(self.client.post(f"/m/quote/{quote.pk}/borrar/").status_code, 404)
        self.assertEqual(self.client.delete(f"/presupuestos/{quote.pk}/").status_code, 200)  # no hay acción de borrado
        self.assertTrue(Quote.objects.filter(pk=quote.pk).exists())

    def test_send_attaches_pdf_and_logs_activity(self):
        self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form())
        quote = Quote.objects.get()
        mail.outbox.clear()
        self.opp.assignees.add(self.luis)
        self.assertContains(self.client.get(f"/presupuestos/{quote.pk}/enviar/"), self.luis.email)
        response = self.client.post(f"/presupuestos/{quote.pk}/enviar/", {
            "to": "cliente@acme.es, otra@acme.es", "cc": "", "subject": "Presupuesto P26 000002", "body": "Adjunto."})
        self.assertEqual(response.status_code, 204)
        message = mail.outbox[0]
        self.assertEqual((message.to, message.reply_to), (["cliente@acme.es", "otra@acme.es"], [self.ana.email]))
        self.assertEqual(message.cc, [self.ana.email, self.luis.email])  # responsable y asignados, aunque el campo llegue vacío
        self.assertEqual(message.attachments[0][2], "application/pdf")
        quote.refresh_from_db()
        self.assertEqual(quote.status, "sent")
        self.assertIsNotNone(quote.sent_at)
        self.assertTrue(Activity.objects.filter(opportunity=self.opp, kind="email", direction="out").exists())
        bad = self.client.post(f"/presupuestos/{quote.pk}/enviar/", {"to": "no-es-correo", "subject": "x", "body": "y"})
        self.assertEqual((bad.status_code, len(mail.outbox)), (200, 1))

    def test_other_salesperson_cannot_see_or_edit(self):
        self.client.post(f"/presupuestos/nuevo/?opportunity={self.opp.pk}", self.form())
        quote = Quote.objects.get()
        self.client.force_login(self.luis)
        self.assertNotContains(self.client.get("/presupuestos/"), f"/presupuestos/{quote.pk}/")
        for url in (f"/presupuestos/{quote.pk}/", f"/presupuestos/{quote.pk}/editar/", f"/presupuestos/{quote.pk}/enviar/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.get(f"/presupuestos/nuevo/?opportunity={self.opp.pk}").status_code, 404)


class LeadDialogTests(Base):
    """Alta de oportunidad: buscar empresa y contacto, o crearlos sin salir del diálogo."""

    def setUp(self):
        self.client.force_login(self.ana)
        self.stage = self.stages["Nuevo lead"].pk

    def post(self, **data):
        base = {"title": "Lead nuevo", "stage": self.stage, "owner": self.ana.pk, "priority": 2}
        return self.client.post("/m/opportunity/", {**base, **data})

    def test_lookup_is_scoped_and_searches(self):
        found = self.client.get("/buscar/company/", {"q": "acme"}).json()["results"]
        self.assertEqual([r["label"] for r in found], ["Fibra Acme, S.L."])
        self.assertEqual(self.client.get("/buscar/company/", {"q": "Redes Luis"}).json()["results"], [])  # es de otro comercial
        Contact.objects.create(company=self.acme, first_name="Marta", last_name="Gil", email="marta@acme.es")
        Contact.objects.create(company=self.other, first_name="Marta", last_name="Oculta")
        found = self.client.get("/buscar/contact/", {"q": "marta gil"}).json()["results"]
        self.assertEqual([(r["label"], r["company"]["id"]) for r in found], [("Marta Gil", self.acme.pk)])
        self.assertEqual(self.client.get("/buscar/otra/").status_code, 404)

    def test_creates_company_and_contact_inline(self):
        response = self.post(new_company="Telecable Norte", new_contact_first_name="Laura", new_contact_last_name="Pérez", new_contact_email="Laura@Telecable.es")
        opp = Opportunity.objects.get(title="Lead nuevo")
        self.assertEqual(response.headers["HX-Redirect"], f"/oportunidades/{opp.pk}/")
        self.assertEqual((opp.company.name, opp.company.owner, opp.contact.email, opp.contact.company), ("Telecable Norte", self.ana, "laura@telecable.es", opp.company))
        self.assertTrue(opp.contact.is_primary)

    def test_new_company_keeps_the_event_where_it_was_met(self):
        from apps.crm.models import Event
        fair = Event.objects.create(name="Aotec 2026")
        self.assertContains(self.client.get("/m/opportunity/"), "Conocida en (feria o evento)")
        self.post(new_company="Telecable Norte", new_company_event=fair.pk)
        self.assertEqual(Opportunity.objects.get(title="Lead nuevo").company.event, fair)

    def test_new_company_and_contact_take_their_main_fields(self):
        dialog = self.client.get("/m/opportunity/").content.decode()
        for name in ("new_company_tax_id", "new_company_city", "new_company_sector", "new_contact_position", "new_contact_legal_basis"):
            self.assertIn(f'name="{name}"', dialog)
        self.post(new_company="Telecable Norte", new_company_kind="customer", new_company_tax_id="b-87.654.321", new_company_city="Oviedo",
                  new_company_province="Asturias", new_company_website="https://telecable.es", new_company_phone="985000000",
                  new_contact_first_name="Laura", new_contact_position="CTO", new_contact_phone="985111111", new_contact_legal_basis="legitimate")
        opp = Opportunity.objects.get(title="Lead nuevo")
        c, p = opp.company, opp.contact
        self.assertEqual((c.kind, c.tax_id, c.city, c.province, c.phone, c.domain), ("customer", "B87654321", "Oviedo", "Asturias", "985000000", "telecable.es"))
        self.assertEqual((p.position, p.phone, p.legal_basis), ("CTO", "985111111", "legitimate"))
        # misma empresa por CIF desde el diálogo de contacto: no se duplica y se completa lo que faltaba
        base = {"first_name": "Pedro", "owner": self.ana.pk, "legal_basis": Contact._meta.get_field("legal_basis").default}
        self.client.post("/m/contact/", {**base, "new_company": "Telecable del Norte SA", "new_company_tax_id": "B87654321", "new_company_sector": "Telecomunicaciones"})
        self.assertEqual(Contact.objects.get(first_name="Pedro").company, c)
        c.refresh_from_db()
        self.assertEqual((c.sector, Company.objects.filter(tax_id="B87654321").count()), ("Telecomunicaciones", 1))

    def test_existing_company_is_reused_not_duplicated(self):
        self.post(new_company="FIBRA ACME SL")
        self.assertEqual(Opportunity.objects.get(title="Lead nuevo").company, self.acme)
        self.assertEqual(Company.objects.filter(name_key=self.acme.name_key).count(), 1)

    def test_contact_dialog_creates_its_company(self):
        from apps.crm.models import Event
        fair = Event.objects.create(name="Aotec 2026")
        self.assertContains(self.client.get("/m/contact/"), 'data-create="new_company"')
        base = {"first_name": "Laura", "owner": self.ana.pk, "legal_basis": Contact._meta.get_field("legal_basis").default}
        self.client.post("/m/contact/", {**base, "new_company": "Telecable Norte", "new_company_event": fair.pk})
        laura = Contact.objects.get(first_name="Laura")
        self.assertEqual((laura.company.name, laura.company.owner, laura.company.event), ("Telecable Norte", self.ana, fair))
        # una que ya existe no se duplica, y sin empresa el contacto sigue valiendo
        self.client.post("/m/contact/", {**base, "first_name": "Pedro", "new_company": "FIBRA ACME SL"})
        self.assertEqual(Contact.objects.get(first_name="Pedro").company, self.acme)
        self.client.post("/m/contact/", {**base, "first_name": "Suelta"})
        self.assertIsNone(Contact.objects.get(first_name="Suelta").company)

    def test_company_is_needed_and_contact_must_match(self):
        self.assertContains(self.post(), "Elige una empresa de la lista o crea una nueva.")
        mine = Company.objects.create(name="Otra de Ana", owner=self.ana)
        outsider = Contact.objects.create(company=mine, first_name="Pepe", owner=self.ana)
        self.assertContains(self.post(company=self.acme.pk, contact=outsider.pk), "es de otra empresa")
        self.assertContains(self.post(company=self.acme.pk, new_contact_email="x@acme.es"), "Indica al menos el nombre")
        self.assertFalse(Opportunity.objects.filter(title="Lead nuevo").exists())


class FreeLineTests(Base):
    """Líneas sin producto del catálogo: recurrentes, por horas o de pago único."""

    def setUp(self):
        self.opp = self.make_opp()
        self.client.force_login(self.ana)

    def add(self, **data):
        return self.client.post(f"/m/line/?opportunity={self.opp.pk}", {"quantity": "1", "discount": "0", "period": "monthly", **data})

    def test_free_lines_feed_their_own_totals(self):
        self.add(billing_model="monthly", description="Cuota módulo a medida", unit_price="300")
        self.add(billing_model="hourly", description="Consultoría", quantity="10", unit_price="70")
        self.add(billing_model="one_off", description="Migración", unit_price="1200")
        self.opp.refresh_from_db()
        self.assertEqual((self.opp.amount_mrr, self.opp.amount_services, self.opp.amount_one_off), (Decimal("300.00"), Decimal("700.00"), Decimal("1200.00")))
        self.assertEqual(sorted(self.opp.lines.values_list("unit", flat=True)), ["", "hora", "mes"])

    def test_free_line_needs_type_and_description(self):
        self.assertContains(self.add(description="Algo", unit_price="5"), "Elige el tipo de cobro")
        self.assertContains(self.add(billing_model="one_off", unit_price="5"), "Describe la línea")
        self.assertFalse(self.opp.lines.exists())

    def test_product_still_sets_its_own_type(self):
        self.add(product=self.training.pk, quantity="10", unit_price="60")
        self.assertEqual(self.opp.lines.get().billing_model, "hourly")

    def test_winning_turns_free_recurring_line_into_subscription(self):
        self.add(billing_model="monthly", description="Cuota módulo a medida", unit_price="300")
        self.client.post(f"/oportunidades/{self.opp.pk}/mover/", {"stage": self.stages["Ganada"].pk})
        sub = Subscription.objects.get()
        self.assertEqual((sub.product, sub.plan_name, sub.mrr), (None, "Cuota módulo a medida", Decimal("300.00")))
        self.assertContains(self.client.get("/suscripciones/"), "Cuota módulo a medida")


class ApkTests(Base):
    def test_apk_needs_a_session(self):
        response = self.client.get("/app/android/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/entrar/", response["Location"])
        self.client.force_login(self.ana)
        # el APK no viaja en el repositorio: cada instalación compila y firma el suyo (android/build.sh)
        import tempfile
        from pathlib import Path
        from unittest import mock

        from apps.crm import views

        with tempfile.TemporaryDirectory() as folder:
            apk = Path(folder) / "qopencrm.apk"
            with mock.patch.object(views, "APK_PATH", apk):
                self.assertEqual(self.client.get("/app/android/").status_code, 404)
                self.assertNotContains(self.client.get("/ajustes/"), "Descargar la app")
                apk.write_bytes(b"PK\x03\x04demo")
                apk.with_name("VERSION").write_text("1.0.0\n")
                response = self.client.get("/app/android/")
                self.assertEqual(response["Content-Type"], "application/vnd.android.package-archive")
                self.assertIn("QopenCRM-1.0.0", response["Content-Disposition"])
                self.assertTrue(b"".join(response.streaming_content).startswith(b"PK"))
                self.assertContains(self.client.get("/ajustes/"), "Descargar la app")


class EventTests(Base):
    """Ferias y eventos: alta, edición, asignación a empresas y recuentos según lo que cada uno ve."""

    def test_only_event_managers_change_events(self):
        from apps.crm.models import Event

        boss_role = Role.objects.get(code="responsable-comercial")
        marta = User.objects.create_user("marta@example.com", name="Marta Resp", role=boss_role)
        self.client.force_login(self.ana)  # un comercial consulta, no modifica
        self.assertEqual(self.client.post("/m/event/", {"name": "ASLAN 2026", "kind": "fair"}).status_code, 403)
        self.assertNotContains(self.client.get("/eventos/"), "/m/event/")
        self.client.force_login(marta)
        response = self.client.post("/m/event/", {"name": "ASLAN 2026", "kind": "fair", "starts_on": "2026-03-18", "ends_on": "2026-03-19", "place": "Madrid"})
        event = Event.objects.get()
        self.assertEqual(response.headers["HX-Redirect"], f"/eventos/{event.pk}/")
        self.assertContains(self.client.post("/m/event/", {"name": "Otra", "kind": "fair", "starts_on": "2026-03-19", "ends_on": "2026-03-18"}), "No puede terminar antes")
        self.client.post(f"/m/event/{event.pk}/", {"name": "ASLAN 2026", "kind": "fair", "place": "IFEMA"})
        event.refresh_from_db()
        self.assertEqual(event.place, "IFEMA")
        # asignar la feria a una empresa propia sí puede hacerlo cualquiera
        self.client.force_login(self.ana)
        self.client.post(f"/m/company/{self.acme.pk}/", {"name": self.acme.name, "kind": "prospect", "country": "España", "owner": self.ana.pk, "event": event.pk})
        self.acme.refresh_from_db()
        self.assertEqual(self.acme.event, event)
        self.make_opp()
        page = self.client.get(f"/eventos/{event.pk}/")
        self.assertContains(page, "Fibra Acme")
        self.assertContains(page, "Despliegue FTTH")
        self.assertContains(self.client.get(f"/cuentas/?event={event.pk}"), "Fibra Acme")
        self.assertEqual(self.client.post(f"/m/event/{event.pk}/borrar/").status_code, 403)
        self.client.force_login(self.luis)  # otro comercial ve la feria, pero no las empresas que no son suyas
        self.assertNotContains(self.client.get(f"/eventos/{event.pk}/"), "Fibra Acme")
        self.client.force_login(marta)  # borrar la feria no borra las empresas
        self.client.post(f"/m/event/{event.pk}/borrar/")
        self.acme.refresh_from_db()
        self.assertIsNone(self.acme.event)

    def test_management_and_sales_managers_create_events(self):
        from apps.crm.models import Event

        for code in ("ceo", "director", "responsable-comercial"):
            user = User.objects.create_user(f"{code}@example.com", name=code, role=Role.objects.get(code=code))
            self.client.force_login(user)
            self.assertContains(self.client.get("/eventos/"), "/m/event/")
            self.client.post("/m/event/", {"name": f"Feria de {code}", "kind": "fair"})
            self.assertTrue(Event.objects.filter(name=f"Feria de {code}", created_by=user).exists())

    def test_listed_by_date(self):
        from apps.crm.models import Event

        today = timezone.localdate()
        for name, delta in (("Lejana", 200), ("Próxima", 10), ("Antigua", -300), ("Reciente", -20)):
            Event.objects.create(name=name, starts_on=today + timedelta(days=delta))
        Event.objects.create(name="Sin día")
        self.client.force_login(self.ana)
        html = self.client.get("/eventos/").content.decode()
        order = [html.index(f"<b>{name}</b>") for name in ("Próxima", "Lejana", "Reciente", "Antigua", "Sin día")]
        self.assertEqual(order, sorted(order))


class HoldTests(Base):
    """Parada por el cliente: sigue en su etapa, con fecha de revisión obligatoria y tope de 90 días."""

    def setUp(self):
        self.opp = self.make_opp()
        self.client.force_login(self.ana)
        self.today = timezone.localdate()

    def hold(self, days, reason="Esperan presupuesto de 2027"):
        return self.client.post(f"/oportunidades/{self.opp.pk}/parar/", {"until": str(self.today + timedelta(days=days)), "reason": reason})

    def test_review_date_is_mandatory_and_capped(self):
        self.assertContains(self.client.post(f"/oportunidades/{self.opp.pk}/parar/", {"reason": "x"}), "requerido")
        self.assertContains(self.hold(0), "posterior a hoy")
        self.assertContains(self.hold(91), "como máximo 90 días")
        self.assertContains(self.hold(30, reason=""), "requerido")
        self.opp.refresh_from_db()
        self.assertFalse(self.opp.on_hold)
        self.assertEqual(self.hold(90).status_code, 204)

    def test_pipeline_defaults_to_mine(self):
        """Aunque el rol lo vea todo, el pipeline abre con lo propio; «Todo el equipo» lo amplía."""
        Opportunity.objects.create(title="Cosa de la jefa", company=self.other, stage=self.opp.stage, owner=self.boss)
        self.client.force_login(self.boss)
        mine = self.client.get("/pipeline/")
        self.assertContains(mine, "Cosa de la jefa")
        self.assertNotContains(mine, self.opp.title)
        self.assertContains(self.client.get("/pipeline/?assignee="), self.opp.title)
        self.assertContains(self.client.get(f"/pipeline/?assignee={self.ana.pk}"), self.opp.title)

    def test_hold_keeps_stage_and_schedules_review(self):
        stage = self.opp.stage
        self.hold(30)
        self.opp.refresh_from_db()
        self.assertEqual((self.opp.stage, self.opp.on_hold, self.opp.hold_count), (stage, True, 1))
        review = self.opp.tasks.get(done_at__isnull=True)
        self.assertEqual((timezone.localtime(review.due_at).date(), review.assignee), (self.today + timedelta(days=30), self.ana))
        self.assertEqual(timezone.localtime(self.opp.next_action_at).date(), self.today + timedelta(days=30))
        self.assertContains(self.client.get("/pipeline/?hold=only"), "on-hold")
        self.assertNotContains(self.client.get("/pipeline/?hold=hide"), "on-hold")
        self.assertContains(self.client.get("/pipeline/"), "Solo paradas · 1")
        self.assertContains(self.client.get("/pipeline/"), "on-hold")
        self.hold(60)  # volver a parar sustituye la revisión, no acumula tareas
        self.opp.refresh_from_db()
        self.assertEqual((self.opp.hold_count, self.opp.tasks.filter(done_at__isnull=True).count()), (2, 1))

    def test_resume_and_close_clear_the_hold(self):
        self.hold(30)
        self.client.post(f"/oportunidades/{self.opp.pk}/reanudar/")
        self.opp.refresh_from_db()
        self.assertEqual((self.opp.on_hold, self.opp.next_action_at, self.opp.tasks.filter(done_at__isnull=True).count()), (False, None, 0))
        self.hold(30)
        self.client.post(f"/oportunidades/{self.opp.pk}/mover/", {"stage": self.stages["Ganada"].pk})
        self.opp.refresh_from_db()
        self.assertIsNone(self.opp.on_hold_until)
        self.assertEqual(self.hold(30).status_code, 404)  # cerrada: ya no se puede parar

    def test_someone_else_cannot_hold_it(self):
        self.client.force_login(self.luis)
        self.assertEqual(self.hold(30).status_code, 404)


class DemoSeedTests(TestCase):
    """Los datos de demostración cargan enteros en una instalación vacía y no se mezclan con datos reales."""

    def test_demo_loads_once(self):
        from io import StringIO

        from django.core.management.base import CommandError

        call_command("seed_demo", stdout=StringIO())
        self.assertEqual((Company.objects.count(), Opportunity.objects.count()), (8, 8))
        self.assertTrue(Opportunity.objects.filter(stage__is_won=True, subscriptions__isnull=False).exists())
        self.assertEqual(Opportunity.objects.filter(on_hold_until__isnull=False).count(), 1)
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())


class MentionTests(Base):
    """@usuario en notas y tareas: aviso en el CRM y correo a quien se nombra."""

    def setUp(self):
        self.client.force_login(self.ana)

    def test_note_mention_notifies_and_emails(self):
        from apps.crm.models import Notification
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/actividad/", {"company": self.acme.pk, "kind": "note",
                                                        "body": "Revisa esto @luis y @jefe, copia a ana@acme.es y a @nadie"})
        self.assertContains(response, '<span class="mention">@luis</span>')
        self.assertEqual(sorted(n.recipient.email for n in Notification.objects.all()), ["jefe@example.com", "luis@example.com"])
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), ["jefe@example.com", "luis@example.com"])
        self.assertEqual(mail.outbox[0].subject, "[QopenCRM] Ana Ventas te ha mencionado · Fibra Acme, S.L.")
        # Luis ve el aviso, lo abre y queda leído
        self.client.force_login(self.luis)
        home = self.client.get("/").content.decode()
        self.assertIn("te ha mencionado", home)  # últimas menciones en Hoy, encima de la actividad
        self.assertLess(home.index("<h2>Menciones</h2>"), home.index("Última actividad"))
        self.assertLess(home.index("Última actividad"), home.index("Sin próxima acción"))
        note = Notification.objects.get(recipient=self.luis)
        self.assertContains(self.client.get("/avisos/"), "Revisa esto")
        self.assertRedirects(self.client.get(f"/avisos/{note.pk}/"), f"/cuentas/{self.acme.pk}/", fetch_redirect_response=False)
        note.refresh_from_db()
        self.assertIsNotNone(note.read_at)
        self.assertEqual(self.client.get(f"/avisos/{Notification.objects.get(recipient=self.boss).pk}/").status_code, 404)

    def test_self_mention_and_edits_do_not_repeat(self):
        from apps.crm.models import Notification
        opp = self.make_opp()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/m/task/", {"title": "Llamar", "kind": "todo", "due_at": "2026-12-01T09:00", "assignee": self.ana.pk,
                                          "opportunity": opp.pk, "notes": "Yo @ana y @luis"})
        task = Task.objects.get(title="Llamar")
        self.assertEqual([n.recipient for n in Notification.objects.all()], [self.luis])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(f"/m/task/{task.pk}/", {"title": "Llamar", "kind": "todo", "due_at": "2026-12-01T09:00", "assignee": self.ana.pk,
                                                     "opportunity": opp.pk, "notes": "Yo @ana y @luis, y ahora @jefe"})
        self.assertEqual(sorted(n.recipient.email for n in Notification.objects.all()), ["jefe@example.com", "luis@example.com"])
        self.assertEqual(Notification.objects.get(recipient=self.boss).url, f"/oportunidades/{opp.pk}/")
        self.assertEqual(mail.outbox[-1].subject, "[QopenCRM] Ana Ventas te ha mencionado · Fibra Acme, S.L. · Despliegue FTTH")

    def test_everyone_notifies_lead_company_and_contact_people(self):
        from apps.crm.models import Contact, Notification

        marta = User.objects.create_user("marta@example.com", name="Marta Ventas", role=self.ana.role)
        pablo = User.objects.create_user("pablo@example.com", name="Pablo Ventas", role=self.ana.role)
        fuera = User.objects.create_user("fuera@example.com", name="Otro Ventas", role=self.ana.role)
        self.acme.owner = self.luis
        self.acme.save()
        contact = Contact.objects.create(company=self.acme, first_name="Greta", owner=marta)
        opp = self.make_opp(contact=contact)  # responsable: Ana, que es quien escribe
        opp.assignees.add(pablo, self.luis)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post("/actividad/", {"company": self.acme.pk, "opportunity": opp.pk, "kind": "note", "body": "Ojo @todos: piden NDA"})
        self.assertContains(response, '<span class="mention">@todos</span>')
        emails = ["luis@example.com", "marta@example.com", "pablo@example.com"]  # sin repetir a Luis, sin Ana y sin quien no tiene que ver
        self.assertEqual(sorted(n.recipient.email for n in Notification.objects.all()), emails)
        self.assertEqual(sorted(m.to[0] for m in mail.outbox), emails)
        self.assertFalse(Notification.objects.filter(recipient=fuera).exists())
        # otro lo escribe: también avisa a Ana, la responsable del lead
        self.client.force_login(self.luis)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post("/actividad/", {"company": self.acme.pk, "opportunity": opp.pk, "kind": "note", "body": "@todos y @jefe, hecho"})
        self.assertEqual(sorted(n.recipient.email for n in Notification.objects.filter(actor=self.luis)),
                         ["ana@example.com", "jefe@example.com", "marta@example.com", "pablo@example.com"])
        self.assertEqual(self.client.get("/buscar/user/", {"q": "to"}).json()["results"][0]["handle"], "todos")

    def test_user_lookup_gives_handles(self):
        found = self.client.get("/buscar/user/", {"q": "luis"}).json()["results"]
        self.assertEqual([(r["label"], r["handle"]) for r in found], [("Luis Ventas", "luis")])


class StripePriceTests(Base):
    """Precios mensual, anual y por usuario desde Stripe, sin duplicar productos."""

    @staticmethod
    def price(name, interval, flat, users, product_id="prod_1", active=True):
        return {"id": f"price_{name}_{interval}", "currency": "eur", "recurring": {"interval": interval},
                "product": {"id": product_id, "name": name, "active": active, "description": ""},
                "tiers": [{"up_to": 1, "unit_amount": 0, "flat_amount": flat}, {"up_to": None, "unit_amount": users, "flat_amount": flat}]}

    def test_sync_updates_existing_and_creates_missing(self):
        from apps.catalog import stripe
        prices = [
            self.price("Atlas TELCO - PRO", "month", 34900, 785, "prod_pro"), self.price("Atlas TELCO - PRO", "year", 376920, 8477, "prod_pro"),
            self.price("Atlas TELCO - VIEW", "month", 2990, 158, "prod_view"), self.price("Atlas TELCO - VIEW", "year", 32292, 1701, "prod_view"),
            self.price("Atlas TELCO - FREE", "month", 0, 0, "prod_old", active=False),
            {**self.price("Otra cosa", "month", 100, 0, "prod_usd"), "currency": "usd"},
        ]
        self.pro.code = "ATLAS-TELCO-PRO"
        self.pro.save()
        before = Product.objects.count()
        result = stripe.sync(prices)
        self.pro.refresh_from_db()
        self.assertEqual((self.pro.price, self.pro.price_annual, self.pro.stripe_product_id), (Decimal("349.00"), Decimal("3769.20"), "prod_pro"))
        self.assertEqual(self.pro.user_tiers, [{"up_to": None, "month": 7.85, "year": 84.77}])
        self.assertEqual([(p.code, p.family.name.upper(), p.tier_level) for p in result["created"]], [("ATLAS-TELCO-VIEW", "ATLAS TELCO", 0)])
        self.assertEqual(Product.objects.count(), before + 1)
        # la segunda pasada casa por id de Stripe aunque cambien código o nombre: no duplica
        Product.objects.filter(code="ATLAS-TELCO-VIEW").update(code="VIEW", name="Visor")
        result = stripe.sync(prices)
        self.assertEqual((len(result["created"]), len(result["same"])), (0, 2))
        self.assertEqual(Product.objects.count(), before + 1)

    def test_button_needs_catalog_permission(self):
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post("/productos/stripe/").status_code, 403)
        self.client.force_login(self.boss)
        with override_settings(STRIPE_SECRET_KEY=""):
            response = self.client.post("/productos/stripe/", follow=True)
        self.assertContains(response, "Falta STRIPE_SECRET_KEY")


class LeadHistoryTests(Base):
    """Cambios en los leads al historial, y lo que ve cada rol en Hoy."""

    def test_lead_edits_and_lines_are_recorded(self):
        self.client.force_login(self.ana)
        opp = self.make_opp()
        form = {"title": "Despliegue FTTH", "company": self.acme.pk, "stage": opp.stage_id, "owner": self.luis.pk, "priority": 3, "probability": 40}
        self.client.post(f"/m/opportunity/{opp.pk}/", form)
        change = Activity.objects.get(opportunity=opp, subject__startswith="Lead modificado")
        self.assertIn("Responsable: Ana Ventas → Luis Ventas", change.body)
        self.assertIn("responsable", change.subject)
        self.client.post(f"/m/opportunity/{opp.pk}/", form)  # sin cambios: no ensucia el historial
        self.assertEqual(Activity.objects.filter(opportunity=opp, subject__startswith="Lead modificado").count(), 1)
        self.client.force_login(self.boss)
        self.client.post(f"/m/line/?opportunity={opp.pk}", {"product": self.pro.pk, "quantity": "2", "discount": "0", "period": "monthly", "unit_price": "500"})
        line = opp.lines.get()
        self.client.post(f"/m/line/{line.pk}/borrar/")
        self.assertEqual(list(Activity.objects.filter(opportunity=opp, subject__startswith="Línea").values_list("subject", flat=True).order_by("id")),
                         ["Línea añadida: Telco Pro", "Línea quitada: Telco Pro"])

    def test_home_by_role(self):
        Contact.objects.create(company=self.other, first_name="Marta", last_name="Oculta")
        self.client.force_login(self.boss)
        home = self.client.get("/")
        self.assertContains(home, "Últimas empresas")
        self.assertContains(home, "Marta Oculta")
        self.assertContains(home, "Todo el equipo · etapas y cambios de leads")
        self.assertContains(home, ">Leads<", html=False)
        self.client.force_login(self.ana)
        home = self.client.get("/")
        self.assertNotContains(home, "Últimas empresas")
        self.assertNotContains(home, "Todo el equipo · etapas")


class ContactManyTests(Base):
    """Una persona en varias empresas, con varios correos y teléfonos."""

    def setUp(self):
        self.client.force_login(self.boss)

    def save_contact(self, pk=None, **data):
        base = {"first_name": "Marta", "last_name": "Gil", "owner": self.ana.pk, "legal_basis": "legitimate",
                "company": self.acme.pk, "email": "Marta@Acme.es", "mobile": "600000000"}
        return self.client.post(f"/m/contact/{pk}/" if pk else "/m/contact/", {**base, **data})

    def test_several_companies_emails_and_phones(self):
        self.save_contact(other_companies=[self.other.pk, self.acme.pk], extra_emails="marta@gmail.com\nMARTA@consultora.es",
                          extra_phones="91 000 00 00 centralita\n600000000")
        marta = Contact.objects.get(first_name="Marta")
        self.assertEqual(list(marta.other_companies.all()), [self.other])  # la principal no se repite
        self.assertEqual(marta.values("email"), ["marta@acme.es", "marta@gmail.com", "marta@consultora.es"])
        self.assertEqual(marta.values("phone"), ["600000000", "91 000 00 00 centralita"])
        # sale en la ficha de las dos empresas y se puede usar en un lead de cualquiera
        page = self.client.get(f"/cuentas/{self.other.pk}/")
        self.assertContains(page, "Marta Gil")
        self.assertContains(page, "También en esta empresa")
        self.assertContains(page, 'href="tel:910000000"')
        Opportunity.objects.create(title="Otro lead", company=self.other, stage=self.stages["Nuevo lead"], owner=self.luis)
        response = self.client.post("/m/opportunity/", {"title": "Lead de Redes", "company": self.other.pk, "contact": marta.pk,
                                                         "stage": self.stages["Nuevo lead"].pk, "owner": self.luis.pk, "priority": 2})
        self.assertEqual(Opportunity.objects.get(title="Lead de Redes").contact, marta, response.content[:300])
        # Luis la ve por su empresa y la encuentra por un correo secundario
        self.client.force_login(self.luis)
        self.assertEqual([r["label"] for r in self.client.get("/buscar/contact/", {"q": "consultora"}).json()["results"]], ["Marta Gil"])
        self.assertEqual([r["label"] for r in self.client.get("/buscar/contact/", {"company": self.other.pk}).json()["results"]], ["Marta Gil"])
        # editar conserva y permite cambiar los adicionales; un correo mal escrito se avisa
        self.client.force_login(self.boss)
        self.assertContains(self.client.get(f"/m/contact/{marta.pk}/"), "marta@gmail.com")
        self.assertContains(self.save_contact(marta.pk, extra_emails="esto no es un correo"), "No parece un correo")
        self.save_contact(marta.pk, other_companies=[self.other.pk], extra_emails="marta@gmail.com")
        self.assertEqual(marta.values("email"), ["marta@acme.es", "marta@gmail.com"])
        self.assertEqual(marta.values("phone"), ["600000000"])

    def test_mail_capture_and_erase_use_all_addresses(self):
        from apps.integrations.mail import Matcher
        marta = Contact.objects.create(company=self.acme, first_name="Marta", email="marta@acme.es")
        marta.set_channels("email", ["Marta@Gmail.com"])
        self.assertEqual(Matcher().contact_for("marta@gmail.com"), marta)
        marta.erase()
        self.assertEqual(marta.values("email"), [])


class SharedTaskTests(Base):
    """Una próxima acción para varias personas."""

    def test_quick_next_for_several_people(self):
        opp = self.make_opp(owner=self.ana)
        self.client.force_login(self.ana)
        self.client.post("/tareas/rapida/", {"title": "Demo con el cliente", "opportunity": opp.pk, "quick": "1",
                                             "assignee": [self.luis.pk, self.ana.pk, self.boss.pk]})
        task = Task.objects.get(title="Demo con el cliente")
        self.assertEqual(task.assignee, self.ana)  # quien la crea, si se ha marcado, es la responsable
        self.assertEqual(set(task.assignees.all()), {self.luis, self.boss})
        self.assertIsNotNone(Opportunity.objects.get(pk=opp.pk).next_action_at)
        # Luis la ve en su Hoy aunque no sea su lead, y puede marcarla
        self.client.force_login(self.luis)
        self.assertContains(self.client.get("/?"), "Demo con el cliente")
        self.assertContains(self.client.get("/tareas/?when=week"), "Demo con el cliente")
        self.client.post(f"/tareas/{task.pk}/hecha/", {"ask_next": "0"})
        self.assertIsNotNone(Task.objects.get(pk=task.pk).done_at)

    def test_reminder_goes_to_everyone(self):
        task = Task.objects.create(title="Llamar juntos", due_at=timezone.now() + timedelta(minutes=5), assignee=self.ana)
        task.assignees.set([self.luis])
        mail.outbox.clear()
        call_command("send_reminders", verbosity=0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(set(mail.outbox[0].to), {self.ana.email, self.luis.email})

    def test_task_form_keeps_main_person_out_of_the_others(self):
        self.client.force_login(self.boss)
        self.client.post("/m/task/", {"title": "Preparar oferta", "kind": "todo", "due_at": "2026-12-01T09:00", "assignee": self.ana.pk,
                                      "assignees": [self.ana.pk, self.luis.pk]})
        task = Task.objects.get(title="Preparar oferta")
        self.assertEqual([u.pk for u in task.people()], [self.ana.pk, self.luis.pk])


class MobileAppTests(Base):
    """La web usada como app en el móvil: ficha de contacto, tarjeta para la agenda, buscador único y compartir."""

    def setUp(self):
        self.marta = Contact.objects.create(company=self.acme, first_name="Marta", last_name="Ruiz; Ortega", position="Directora técnica",
                                            email="marta@acme.es", mobile="612 345 678", phone="954 00 11 22 centralita", owner=self.ana)
        self.marta.set_channels("email", ["m.ruiz@gmail.com"])

    def test_contact_page_puts_the_phone_to_work(self):
        opp = self.make_opp(contact=self.marta)
        Activity.objects.create(company=self.acme, contact=self.marta, user=self.ana, kind="call", body="Quiere demo el martes")
        self.client.force_login(self.ana)
        page = self.client.get(f"/contactos/{self.marta.pk}/")
        for text in ('href="tel:612345678"', "https://wa.me/34612345678", 'href="mailto:marta@acme.es"', "m.ruiz@gmail.com",
                     f"/contactos/{self.marta.pk}/vcard/", opp.title, "Quiere demo el martes", f"/cuentas/{self.acme.pk}/"):
            self.assertContains(page, text)
        self.assertTrue(AccessLog.objects.filter(action="view", object_type="contact", object_id=str(self.marta.pk)).exists())
        # la lista lleva a la ficha y deja llamar sin entrar en ella
        self.assertContains(self.client.get("/contactos/"), f'data-href="/contactos/{self.marta.pk}/"')
        # Luis no lleva esa empresa: no ve a la persona
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(f"/contactos/{self.marta.pk}/").status_code, 404)
        self.assertEqual(self.client.get(f"/contactos/{self.marta.pk}/vcard/").status_code, 404)

    def test_vcard_for_the_phone_book(self):
        self.client.force_login(self.ana)
        response = self.client.get(f"/contactos/{self.marta.pk}/vcard/")
        self.assertEqual(response["Content-Type"], "text/vcard; charset=utf-8")
        self.assertIn(".vcf", response["Content-Disposition"])
        card = response.content.decode()
        for line in ("BEGIN:VCARD", r"N:Ruiz\; Ortega;Marta;;;", r"FN:Marta Ruiz\; Ortega", r"ORG:Fibra Acme\, S.L.", "TITLE:Directora técnica",
                     "TEL;TYPE=CELL:612345678", "TEL;TYPE=WORK:954001122", "EMAIL;TYPE=WORK:marta@acme.es", "EMAIL;TYPE=WORK:m.ruiz@gmail.com", "END:VCARD"):
            self.assertIn(line + "\r\n", card)
        self.assertTrue(AccessLog.objects.filter(action="export", object_type="contact").exists())
        self.marta.erase()  # tras la supresión RGPD ya no hay tarjeta que descargar
        self.assertEqual(self.client.get(f"/contactos/{self.marta.pk}/vcard/").status_code, 404)

    def test_one_search_for_everything(self):
        opp = self.make_opp()
        self.client.force_login(self.ana)
        page = self.client.get("/busqueda/?q=acme")
        self.assertContains(page, f"/cuentas/{self.acme.pk}/")
        self.assertContains(page, f"/oportunidades/{opp.pk}/")
        self.assertContains(page, f"/contactos/{self.marta.pk}/")  # por el nombre de su empresa
        self.assertContains(self.client.get("/busqueda/?q=612 345"), f"/contactos/{self.marta.pk}/")
        self.assertContains(self.client.get("/busqueda/?q=a"), "al menos dos letras")
        partial = self.client.get("/busqueda/?q=marta", headers={"HX-Request": "true"})
        self.assertContains(partial, "Marta Ruiz")
        self.assertNotContains(partial, "<html")
        # cada uno busca solo en lo suyo
        self.client.force_login(self.luis)
        page = self.client.get("/busqueda/?q=acme")
        self.assertNotContains(page, f"/cuentas/{self.acme.pk}/")
        self.assertNotContains(page, f"/oportunidades/{opp.pk}/")

    def test_shared_text_and_app_shortcuts(self):
        self.client.force_login(self.ana)
        page = self.client.get("/compartir/", {"title": "Firma", "text": "Marta Ruiz\n612 345 678", "url": "https://acme.es"})
        self.assertContains(page, "Marta Ruiz\n612 345 678\nhttps://acme.es")
        self.assertContains(page, 'data-share data-modal="/m/contact/"')
        manifest = self.client.get("/manifest.webmanifest").json()
        self.assertEqual(manifest["share_target"]["action"], "/compartir/")
        self.assertIn("/contactos/?nuevo=contact", [s["url"] for s in manifest["shortcuts"]])
        # el alta de contacto ofrece la agenda del móvil; al editar, no
        self.assertContains(self.client.get("/m/contact/"), "data-pick-contact")
        self.assertNotContains(self.client.get(f"/m/contact/{self.marta.pk}/"), "data-pick-contact")
        self.assertContains(self.client.get("/m/contact/"), 'inputmode="tel"')

    def test_app_shell(self):
        opp = self.make_opp(contact=self.marta)
        self.client.force_login(self.ana)
        home = self.client.get("/")
        for text in ('class="appbar"', 'class="tabbar"', 'data-sheet="#more"', 'id="dial"', 'data-depth="0"', "/busqueda/"):
            self.assertContains(home, text)
        lead = self.client.get(f"/oportunidades/{opp.pk}/")
        self.assertContains(lead, 'data-depth="1"')
        self.assertContains(lead, 'href="/pipeline/" data-back')
        self.assertContains(lead, "https://wa.me/34612345678")
        self.assertNotContains(lead, 'id="dial"')
        # una oportunidad sin contacto no rompe la ficha: las acciones salen desactivadas
        bare = self.make_opp()
        self.assertContains(self.client.get(f"/oportunidades/{bare.pk}/"), 'aria-disabled="true"')
        self.assertContains(self.client.get("/pipeline/"), "data-stage-tab")
        self.assertContains(self.client.get("/sw.js"), "Sin conexión")

    def test_whatsapp_number(self):
        from apps.crm.templatetags.crm import wa_href
        self.assertEqual(wa_href("612 345 678"), "34612345678")
        self.assertEqual(wa_href("+34 612 34 56 78 personal"), "34612345678")
        self.assertEqual(wa_href("0033 1 23 45 67 89"), "33123456789")
        self.assertEqual(wa_href(""), "")


class NdaTests(Base):
    """Acuerdo de confidencialidad desde la ficha del lead, en español o en inglés."""

    def setUp(self):
        from apps.crm.models import Contact

        self.acme.address, self.acme.city, self.acme.province, self.acme.country = "Friedrich-Ebert-Straße 75", "Bergisch Gladbach", "", "Alemania"
        self.acme.save()
        contact = Contact.objects.create(company=self.acme, first_name="Greta", last_name="Weber", position="Managing Director")
        self.opp = self.make_opp(contact=contact)
        self.enterContext(self.settings(MEDIA_ROOT=self.enterContext(__import__("tempfile").TemporaryDirectory())))
        self.client.force_login(self.ana)

    def post(self, **data):
        return self.client.post(f"/oportunidades/{self.opp.pk}/nda/", {
            "language": "es", "signed_on": "2026-11-27", "party_name": "Fibra Acme GmbH", "party_address": "Friedrich-Ebert-Straße 75, Bergisch Gladbach",
            "party_tax_id": "DE362031271", "party_country": "Alemania", "signer_name": "Greta Weber", "signer_position": "Managing Director", **data})

    def test_dialog_prefilled_from_company_and_contact(self):
        page = self.client.get(f"/oportunidades/{self.opp.pk}/nda/")
        self.assertContains(page, "Friedrich-Ebert-Straße 75, Bergisch Gladbach, Alemania")
        self.assertContains(page, "Greta Weber")
        self.assertContains(page, "Managing Director")
        self.assertContains(page, "B12345678")

    def test_generates_pdf_in_both_languages(self):
        from apps.sales.models import Nda

        self.assertEqual(self.post().status_code, 204)
        self.assertEqual(self.post(language="en").status_code, 204)
        english, spanish = Nda.objects.all()
        self.assertEqual((spanish.language, english.language), ("es", "en"))
        for nda in (spanish, english):
            response = self.client.get(f"/nda/{nda.pk}/pdf/")
            self.assertEqual(response["Content-Type"], "application/pdf")
            self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))
        self.assertTrue(self.opp.activities.filter(subject__startswith="NDA generado en inglés").exists())
        self.assertContains(self.client.get(f"/oportunidades/{self.opp.pk}/"), "Fibra Acme GmbH")

    def test_text_uses_both_parties_and_configured_courts(self):
        from apps.sales import services
        from apps.sales.models import Nda
        from django.template.loader import render_to_string
        from unittest import mock

        self.post()
        self.post(language="en")
        captured = []
        real = render_to_string
        with mock.patch.object(services, "render_to_string", side_effect=lambda *a, **k: captured.append(real(*a, **k)) or captured[-1]):
            for nda in Nda.objects.order_by("language"):
                services.render_nda_pdf(nda)
        english, spanish = captured
        self.assertIn("Courts and Tribunals of the city of Madrid", english)
        self.assertIn("incorporated under the laws of Germany", english)
        self.assertIn("27 November 2026", english)
        self.assertIn("Juzgados y Tribunales de la ciudad de Madrid", spanish)
        self.assertIn("27 de noviembre de 2026", spanish)
        for text in captured:
            for value in ("TU EMPRESA, S.L.", "B00000000", "Fibra Acme GmbH", "DE362031271", "Greta Weber", "Managing Director"):
                self.assertIn(value, text)

    def test_requires_name_and_address(self):
        from apps.sales.models import Nda

        response = self.post(party_address="")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Nda.objects.exists())

    def test_other_salesperson_cannot_generate_open_or_delete(self):
        from apps.sales.models import Nda

        self.post()
        nda = Nda.objects.get()
        self.client.force_login(self.luis)
        self.assertEqual(self.client.get(f"/oportunidades/{self.opp.pk}/nda/").status_code, 404)
        self.assertEqual(self.client.get(f"/nda/{nda.pk}/pdf/").status_code, 404)
        self.assertEqual(self.client.post(f"/nda/{nda.pk}/borrar/").status_code, 404)
        self.client.force_login(self.ana)
        self.assertEqual(self.client.post(f"/nda/{nda.pk}/borrar/").status_code, 302)
        self.assertFalse(Nda.objects.exists())


class TeamsMeetingCardTests(Base):
    """Las reuniones de Teams convocadas se ven en el historial como una tarjeta con su botón de acceso."""

    URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x%40thread.v2/0?context=%7b%22Tid%22%3a%22t%22%7d"

    def meeting(self, opp, body):
        return Activity.objects.create(company=self.acme, opportunity=opp, user=self.ana, kind=Activity.Kind.MEETING, source="teams",
                                       occurred_at=timezone.make_aware(datetime(2026, 12, 1, 10, 0)), subject="Reunión de Teams convocada: Próximos pasos", body=body)

    def test_card_with_join_button_and_details(self):
        opp = self.make_opp()
        self.meeting(opp, f"Convocada desde ana@example.onmicrosoft.com para paco@acme.es, luis@example.com.\nDuración: 90 min\nEnlace: {self.URL}\n\nTraed el pliego")
        Task.objects.create(title="Reunión de Teams: Próximos pasos", kind=Task.Kind.MEETING, due_at=timezone.now(), assignee=self.ana, opportunity=opp, company=self.acme, notes=self.URL)
        self.client.force_login(self.ana)
        page = self.client.get(f"/oportunidades/{opp.pk}/").content.decode()
        from django.utils.html import escape
        self.assertIn(f'href="{escape(self.URL)}" target="_blank" rel="noopener"><svg', page)
        self.assertIn("Unirse a la reunión", page)
        self.assertIn("mar 1 dic 2026, 10:00–11:30", page)
        self.assertIn("1 h 30 min", page)
        self.assertIn('href="mailto:paco@acme.es"', page)
        self.assertIn("Traed el pliego", page)
        self.assertNotIn("Enlace: https", page)  # el enlace ya no sale como texto suelto
        self.assertIn(">Unirse</a>", page)  # también en la próxima acción

    def test_meetings_convened_before_keep_working(self):
        opp = self.make_opp()
        self.meeting(opp, f"Convocada desde ana@example.onmicrosoft.com para paco@acme.es.\nEnlace: {self.URL}")
        note = Activity.objects.create(company=self.acme, opportunity=opp, user=self.ana, kind=Activity.Kind.MEETING, body="Reunión presencial en su oficina")
        self.client.force_login(self.ana)
        page = self.client.get(f"/oportunidades/{opp.pk}/").content.decode()
        self.assertIn("Unirse a la reunión", page)
        self.assertIn("mar 1 dic 2026, 10:00<", page.replace(" <", "<"))
        self.assertIn(note.body, page)
