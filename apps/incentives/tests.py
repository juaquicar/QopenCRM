"""Ejemplos numéricos de los tres programas de incentivos."""
from decimal import Decimal as D

from django.core.management import call_command
from django.test import TestCase

from apps.accounts.models import Role, User
from apps.catalog.models import Product
from apps.crm.models import Company, Opportunity, OpportunityLine, Stage

from .models import Incentive, ProductivityScore, ProductivityYear


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed", verbosity=0)
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))
        cls.luis = User.objects.create_user("luis@example.com", name="Luis Ventas", role=Role.objects.get(code="comercial"))
        cls.marta = User.objects.create_user("marta@example.com", name="Marta Resp", role=Role.objects.get(code="responsable-comercial"))


class RuleTests(Base):
    def test_sales_new_client_is_five_percent_of_margin_paid_as_collected(self):
        incentive = Incentive(program="sales_new", user=self.ana, concept="x", revenue=D(15000), direct_costs=D(5000), status="approved")
        self.assertEqual((incentive.margin, incentive.total, incentive.earned), (D(10000), D("500.00"), D(0)))
        incentive.collected = D(7500)
        self.assertEqual((incentive.earned, incentive.collected_pct), (D("250.00"), D("50.0")))
        incentive.collected, incentive.paid = D(15000), D(250)
        self.assertEqual((incentive.earned, incentive.due), (D("500.00"), D("250.00")))
        incentive.status = "proposed"  # sin validar no devenga
        self.assertEqual(incentive.earned, D(0))

    def test_recurring_quota_is_the_mean_of_the_first_four_once_the_fourth_is_collected(self):
        incentive = Incentive(program="sales_quota", user=self.ana, concept="x", status="approved",
                              quota_1=D(200), quota_2=D(300), quota_3=D(300), quota_4=D(400), quotas_collected=3)
        self.assertEqual((incentive.total, incentive.earned), (D("300.00"), D(0)))  # baja antes de la cuarta: nada
        incentive.quotas_collected = 4
        self.assertEqual(incentive.earned, D("300.00"))

    def test_pm_needs_satisfaction_and_profitability_then_adds_one_point_each(self):
        incentive = Incentive(program="pm", user=self.ana, concept="x", status="approved", revenue=D(20000), direct_costs=D(8000),
                              planned_margin=D(12000), satisfaction_score=D("8.5"), kpi_deadlines=True, kpi_costs=False, collected=D(20000))
        self.assertEqual((incentive.rate, incentive.total), (D(6), D("720.00")))
        incentive.kpi_costs = True
        self.assertEqual((incentive.rate, incentive.total), (D(7), D("840.00")))
        incentive.satisfaction_score = D("7.9")
        self.assertEqual((incentive.rate, incentive.total), (D(0), D("0.00")))
        incentive.satisfaction_score, incentive.planned_margin = D(9), D(13000)  # margen real por debajo del previsto
        self.assertEqual(incentive.rate, D(0))

    def test_productivity_fund_and_proportional_split(self):
        year = ProductivityYear.objects.create(year=2025, profit=D("151853.85"), previous_profit=D("126544.88"))
        self.assertEqual((year.growth, year.activated, year.fund), (D("20.0"), True, D("15185.39")))
        for user, peer, management in ((self.ana, 8, 8), (self.luis, 7, 6), (self.marta, 5, 5)):
            ProductivityScore.objects.create(year=year, user=user, peer_score=peer, management_score=management)
        shares = {score.user: score for score in year.shares()}
        self.assertEqual([shares[u].final for u in (self.ana, self.luis, self.marta)], [D("8.00"), D("6.50"), D("5.00")])
        self.assertEqual([shares[u].share for u in (self.ana, self.luis, self.marta)], [D("6229.90"), D("5061.80"), D("3893.69")])
        self.assertEqual(sum(s.share for s in shares.values()), year.fund)
        # medio año trabajado pesa la mitad; quien no participa no recibe
        ProductivityScore.objects.filter(user=self.luis).update(months=6)
        ProductivityScore.objects.filter(user=self.marta).update(eligible=False)
        shares = {score.user: score.share for score in year.shares()}
        self.assertEqual((shares[self.marta], shares[self.ana] + shares[self.luis]), (D(0), year.fund))
        self.assertGreater(shares[self.ana], shares[self.luis] * 2)

    def test_fund_needs_growth_unless_management_decides(self):
        year = ProductivityYear.objects.create(year=2026, profit=D(110000), previous_profit=D(100000))
        self.assertEqual((year.growth, year.activated, year.fund), (D("10.0"), False, D(0)))
        year.decision, year.fund_override = "apply", D(5000)
        self.assertEqual(year.fund, D(5000))
        year.decision = "cancel"
        self.assertEqual(year.fund, D(0))


class ScreenTests(Base):
    def setUp(self):
        self.mine = Incentive.objects.create(program="sales_new", user=self.ana, concept="Cliente de Ana", revenue=D(15000), direct_costs=D(5000),
                                             collected=D(15000), status="approved")
        self.theirs = Incentive.objects.create(program="sales_new", user=self.luis, concept="Cliente de Luis", revenue=D(1000), status="proposed")

    def test_salesperson_sees_only_their_own_and_cannot_manage(self):
        self.client.force_login(self.ana)
        page = self.client.get("/incentivos/")
        self.assertContains(page, "Mis incentivos")
        self.assertContains(page, "Cliente de Ana")
        self.assertNotContains(page, "Cliente de Luis")
        self.assertNotContains(self.client.get(f"/incentivos/?persona={self.luis.pk}"), "Cliente de Luis")
        self.assertEqual(self.client.get("/m/incentive_sales/").status_code, 403)
        self.assertEqual(self.client.post(f"/m/incentive_sales/{self.mine.pk}/", {"paid": "500"}).status_code, 403)
        self.assertEqual(self.client.post(f"/incentivos/{self.theirs.pk}/decidir/", {"status": "approved"}).status_code, 403)

    def test_manager_sees_everyone_validates_and_records_payment(self):
        self.client.force_login(self.marta)
        page = self.client.get("/incentivos/")
        self.assertContains(page, "Cliente de Ana")
        self.assertContains(page, "Cliente de Luis")
        self.client.post(f"/incentivos/{self.theirs.pk}/decidir/", {"status": "approved"})
        self.theirs.refresh_from_db()
        self.assertEqual((self.theirs.status, self.theirs.decided_by), ("approved", self.marta))
        response = self.client.post("/m/incentive_pm/", {"user": self.ana.pk, "concept": "Proyecto X", "status": "approved", "revenue": "20000", "direct_costs": "8000",
                                                         "planned_margin": "12000", "satisfaction_score": "9", "kpi_deadlines": "on", "collected": "20000", "paid": "0"})
        self.assertEqual(response.status_code, 204)
        pm = Incentive.objects.get(concept="Proyecto X")
        self.assertEqual((pm.program, pm.total, pm.created_by), ("pm", D("720.00"), self.marta))
        self.assertContains(self.client.post("/m/incentive_pm/", {"user": self.ana.pk, "concept": "Y", "status": "approved", "revenue": "1", "direct_costs": "0",
                                                                  "satisfaction_score": "11", "collected": "0", "paid": "0"}), "de 0 a 10")

    def test_unpublished_productivity_year_is_hidden_from_the_team(self):
        year = ProductivityYear.objects.create(year=2025, profit=D(120000), previous_profit=D(100000))
        ProductivityScore.objects.create(year=year, user=self.ana, peer_score=8, management_score=8)
        ProductivityScore.objects.create(year=year, user=self.luis, peer_score=6, management_score=6)
        self.client.force_login(self.ana)
        self.assertNotContains(self.client.get("/incentivos/"), "Fondo activado")
        year.published = True
        year.save()
        page = self.client.get("/incentivos/")
        self.assertContains(page, "Fondo activado")
        self.assertNotContains(page, "Luis Ventas")  # no ve la nota ni el reparto de los demás
        self.assertNotContains(page, "120.000")  # ni el beneficio de la empresa


class ProposalTests(Base):
    def win(self, owner, kind="prospect"):
        company = Company.objects.create(name=f"Cliente {owner.name} {kind}", owner=owner, kind=kind)
        opp = Opportunity.objects.create(title="Implantación", company=company, stage=Stage.objects.first(), owner=owner)
        plan = Product.objects.create(code=f"P{company.pk}", name="Plan", billing_model="monthly", unit="mes", price=300)
        OpportunityLine.objects.create(opportunity=opp, product=plan, billing_model="monthly", quantity=1, unit_price=300)
        OpportunityLine.objects.create(opportunity=opp, description="Puesta en marcha", billing_model="one_off", quantity=1, unit_price=2000)
        self.client.force_login(owner)
        self.client.post(f"/oportunidades/{opp.pk}/mover/", {"stage": Stage.objects.get(is_won=True).pk})
        return opp

    def test_winning_a_new_client_proposes_both_sales_incentives(self):
        opp = self.win(self.ana)
        proposed = {i.program: i for i in Incentive.objects.filter(opportunity=opp)}
        self.assertEqual(set(proposed), {"sales_new", "sales_quota"})
        self.assertEqual((proposed["sales_new"].revenue, proposed["sales_new"].status, proposed["sales_new"].user), (D("2000.00"), "proposed", self.ana))
        self.assertEqual((proposed["sales_quota"].total, proposed["sales_quota"].earned), (D("300.00"), D(0)))

    def test_existing_customer_or_excluded_owner_gets_no_proposal(self):
        self.win(self.ana, kind="customer")
        boss = User.objects.create_superuser("jefe@example.com", name="Jefa")
        self.win(boss)
        self.assertFalse(Incentive.objects.exists())
