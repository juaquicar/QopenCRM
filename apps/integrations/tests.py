import json
from email.message import EmailMessage

from django.test import TestCase, override_settings

from apps.accounts.models import User
from apps.crm.models import Activity, Company, Contact, Opportunity, Stage
from apps.integrations.mail import Matcher, process
from apps.integrations.models import ApiKey


def build(sender, to, subject, body, message_id, cc="", calendar=None):
    message = EmailMessage()
    message["From"], message["To"], message["Subject"], message["Message-ID"] = sender, to, subject, message_id
    message["Date"] = "Mon, 16 Nov 2026 10:30:00 +0100"
    if cc:
        message["Cc"] = cc
    message.set_content(body)
    if calendar:
        message.add_alternative(calendar, subtype="calendar")
    return message.as_bytes()


@override_settings(MAIL_CAPTURE_USER="sales@example.com")
class MailCaptureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ana = User.objects.create_user("ana@example.com", name="Ana")
        stage = Stage.objects.create(name="Nuevo", order=0)
        cls.acme = Company.objects.create(name="Acme Fibra", website="acme.es", owner=cls.ana)
        cls.marta = Contact.objects.create(company=cls.acme, first_name="Marta", email="marta@acme.es")
        cls.opp = Opportunity.objects.create(title="FTTH", company=cls.acme, stage=stage, owner=cls.ana)

    def test_incoming_and_duplicates(self):
        raw = build("Marta <marta@acme.es>", "ana@example.com", "Re: Propuesta", "Nos encaja.\n\nEl lun, 16 nov escribió:\n> texto citado", "<1@acme>", cc="sales@example.com")
        activity = process(raw, Matcher())
        self.assertEqual((activity.kind, activity.direction, activity.company, activity.contact), ("email", "in", self.acme, self.marta))
        self.assertEqual((activity.user, activity.opportunity, activity.body, activity.subject), (self.ana, self.opp, "Nos encaja.", "Re: Propuesta"))
        self.assertIsNone(process(raw, Matcher()))
        self.assertEqual(Activity.objects.count(), 1)

    def test_outgoing_with_mailbox_in_copy(self):
        raw = build("ana@example.com", "marta@acme.es", "Propuesta", "Adjunto la propuesta.", "<2@example>", cc="sales@example.com")
        activity = process(raw, Matcher())
        self.assertEqual((activity.direction, activity.user, activity.contact), ("out", self.ana, self.marta))

    def test_forwarded_by_team(self):
        body = "Para el CRM.\n\n---------- Forwarded message ---------\nDe: Marta <marta@acme.es>\nAsunto: Dudas\n\nTengo dudas con el precio."
        activity = process(build("ana@example.com", "sales@example.com", "Fwd: Dudas", body, "<3@example>"), Matcher())
        self.assertEqual((activity.contact, activity.direction, activity.user), (self.marta, "in", self.ana))

    def test_new_person_from_known_domain(self):
        activity = process(build("Pedro Soto <pedro@acme.es>", "sales@example.com", "Hola", "Soy el nuevo CTO.", "<4@acme>"), Matcher())
        pedro = Contact.objects.get(email="pedro@acme.es")
        self.assertEqual((pedro.company, pedro.first_name, pedro.last_name, activity.contact), (self.acme, "Pedro", "Soto", pedro))

    def test_strangers_and_internal_mail_are_ignored(self):
        matcher = Matcher()
        self.assertIsNone(process(build("spam@otro.com", "sales@example.com", "Oferta", "Compra ya", "<5@x>"), matcher))
        self.assertIsNone(process(build("alguien@gmail.com", "sales@example.com", "Hola", "Hola", "<6@x>"), matcher))
        self.assertIsNone(process(build("ana@example.com", "sales@example.com", "Interno", "Nota interna", "<7@x>"), matcher))
        self.assertFalse(Activity.objects.exists())

    def test_meeting_invitation(self):
        ics = "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nSUMMARY:Demo Atlas Telco\r\nDTSTART:20261120T100000Z\r\nLOCATION:Teams\r\nEND:VEVENT\r\nEND:VCALENDAR"
        activity = process(build("ana@example.com", "marta@acme.es", "Invitación", "Os paso la convocatoria.", "<8@example>", cc="sales@example.com", calendar=ics), Matcher())
        self.assertEqual((activity.kind, activity.subject), ("meeting", "Demo Atlas Telco"))
        self.assertEqual(activity.occurred_at.isoformat(), "2026-11-20T10:00:00+00:00")
        self.assertIn("Teams", activity.body)


import json
from unittest import mock

from apps.integrations import ai


@override_settings(AI_API_KEY="clave-de-prueba", AI_PROVIDER="openai", AI_MODEL="")
class AssistantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser("jefe@example.com", name="Jefa")
        cls.stage = Stage.objects.create(name="Nuevo", order=0)

    def reply(self, payload, provider="openai"):
        import json
        text = json.dumps(payload)
        return {"choices": [{"message": {"content": text}}]} if provider == "openai" else {"content": [{"type": "text", "text": f"Claro:\n{text}"}]}

    def test_fill_form_from_pasted_text(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get("/m/contact/"), "Rellenar con IA")
        with mock.patch.object(ai, "_post", return_value=self.reply({"first_name": "Marta", "email": "marta@acme.es", "password": "x", "mobile": None})) as post:
            response = self.client.post("/ia/rellenar/contact/", {"text": "Marta · CTO · marta@acme.es"})
        self.assertEqual(response.json(), {"fields": {"first_name": "Marta", "email": "marta@acme.es"}})
        url, headers, payload = post.call_args.args
        self.assertEqual((url, headers["Authorization"], payload["model"]), ("https://api.openai.com/v1/chat/completions", "Bearer clave-de-prueba", "gpt-4o-mini"))
        self.assertEqual(payload["messages"][1]["content"], "Marta · CTO · marta@acme.es")

    def test_fill_form_searching_the_internet(self):
        from apps.crm.models import Company

        self.client.force_login(self.user)
        self.assertContains(self.client.get("/m/company/"), "Buscar en internet")
        self.assertEqual(self.client.post("/ia/internet/company/", {"name": ""}).status_code, 400)
        self.assertEqual(self.client.post("/ia/internet/contact/", {"first_name": "Marta"}).status_code, 400)
        found = {"name": "Acme Fibra, S.L.", "tax_id": "B12345678", "city": "Sevilla", "source": "https://www.acme.es/aviso-legal", "ceo": "x"}
        reply = {"output": [{"type": "web_search_call"}, {"type": "message", "content": [{"type": "output_text", "text": "```json\n" + json.dumps(found) + "\n```"}]}]}
        with mock.patch.object(ai, "_post", return_value=reply) as post:
            response = self.client.post("/ia/internet/company/", {"name": "Acme Fibra", "website": "acme.es", "notes": "no se envía", "text": "operador de Sevilla"})
        self.assertEqual(response.json(), {"fields": {"name": "Acme Fibra, S.L.", "tax_id": "B12345678", "city": "Sevilla"}, "source": "https://www.acme.es/aviso-legal"})
        url, _, payload = post.call_args.args
        self.assertEqual((url, payload["tools"]), ("https://api.openai.com/v1/responses", [{"type": "web_search"}]))
        self.assertIn("nombre: Acme Fibra\nweb: acme.es", payload["input"])
        self.assertIn("operador de Sevilla", payload["input"])
        self.assertNotIn("no se envía", payload["input"])
        # contacto: la empresa elegida viaja por su nombre, no por su id
        company = Company.objects.create(name="Redes del Sur")
        with mock.patch.object(ai, "_post", return_value={"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"position": "CTO"}'}]}]}) as post:
            response = self.client.post("/ia/internet/contact/", {"first_name": "Marta", "last_name": "Ruiz", "company": company.pk})
        self.assertEqual(response.json(), {"fields": {"position": "CTO"}, "source": ""})
        self.assertIn("empresa: Redes del Sur", post.call_args.args[2]["input"])

    def test_internet_search_with_anthropic_resumes_paused_turn(self):
        paused = {"stop_reason": "pause_turn", "content": [{"type": "server_tool_use", "id": "srvtoolu_1", "name": "web_search", "input": {"query": "Acme"}}]}
        done = {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Voy a buscar {la web}."}, {"type": "web_search_tool_result", "content": []},
                                                        {"type": "text", "text": '{"name": "Acme", "source": "ftp://x"}'}]}
        with self.settings(AI_PROVIDER="anthropic"), mock.patch.object(ai, "_post", side_effect=[paused, done]) as post:
            self.assertEqual(ai.lookup("company", {"name": "Acme"}), {"fields": {"name": "Acme"}, "source": ""})
        first, second = (call.args[2] for call in post.call_args_list)
        self.assertEqual(first["tools"][0]["name"], "web_search")
        self.assertEqual(second["messages"][1], {"role": "assistant", "content": paused["content"]})

    def test_natural_language_search(self):
        self.client.force_login(self.user)
        with mock.patch.object(ai, "_post", return_value=self.reply({"action": "none", "assignee": self.user.pk, "priority": 3, "borrar": "todo"})):
            response = self.client.post("/ia/buscar/", {"question": "lo urgente sin próxima acción de la jefa"})
        self.assertEqual(response.url, f"/pipeline/?action=none&assignee={self.user.pk}&priority=3")
        self.assertEqual(self.client.get(response.url).status_code, 200)
        # una respuesta con valores imposibles cae a búsqueda de texto normal
        with mock.patch.object(ai, "_post", return_value=self.reply({"stage": 99999})):
            self.assertEqual(self.client.post("/ia/buscar/", {"question": "telco"}).url, "/pipeline/?q=telco&assignee=")

    def test_anthropic_and_errors(self):
        with self.settings(AI_PROVIDER="anthropic"), mock.patch.object(ai, "_post", return_value=self.reply({"name": "Acme"}, "anthropic")) as post:
            self.assertEqual(ai.extract("company", "Acme SL"), {"name": "Acme"})
            self.assertEqual((post.call_args.args[1]["x-api-key"], post.call_args.args[2]["model"]), ("clave-de-prueba", "claude-haiku-5-5"))
        self.client.force_login(self.user)
        with mock.patch.object(ai, "_post", side_effect=ai.AIError("El proveedor de IA ha respondido 401")):
            self.assertEqual(self.client.post("/ia/rellenar/company/", {"text": "x"}).status_code, 502)
        with self.settings(AI_API_KEY=""):
            self.assertNotContains(self.client.get("/m/contact/"), "Rellenar con IA")
            self.assertNotContains(self.client.get("/pipeline/"), "Buscar con IA")


class McpTests(TestCase):
    """Servidor MCP: mismo alcance y permisos que la persona dueña de la clave."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from apps.accounts.models import Role, User
        from apps.crm.models import Company, Opportunity, Stage

        call_command("seed", verbosity=0)
        cls.boss = User.objects.create_superuser("jefe@example.com", name="Jefa")
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))
        cls.luis = User.objects.create_user("luis@example.com", name="Luis Ventas", role=Role.objects.get(code="comercial"))
        cls.acme = Company.objects.create(name="Fibra Acme, S.L.", owner=cls.ana)
        cls.other = Company.objects.create(name="Redes Luis SA", owner=cls.luis)
        stage = Stage.objects.first()
        cls.mine = Opportunity.objects.create(title="Mía", company=cls.acme, stage=stage, owner=cls.ana)
        cls.theirs = Opportunity.objects.create(title="Ajena", company=cls.other, stage=stage, owner=cls.luis)
        cls.keys = {user: ApiKey.issue(user, "mcp")[1] for user in (cls.boss, cls.ana)}

    def rpc(self, user, method, params=None, **message):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}, **message}
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.keys[user]}"} if user else {}
        return self.client.post("/mcp/", data=json.dumps(body), content_type="application/json", **headers)

    def call(self, user, name, **arguments):
        result = self.rpc(user, "tools/call", {"name": name, "arguments": arguments}).json()["result"]
        text = result["content"][0]["text"]
        return result["isError"], (text if result["isError"] else json.loads(text))

    def test_handshake_and_auth(self):
        self.assertEqual(self.rpc(None, "initialize").status_code, 401)
        self.assertEqual(self.client.get("/mcp/").status_code, 405)
        # la URL sin barra final responde igual, sin redirección (un POST redirigido no lo sigue el conector)
        self.assertEqual(self.client.post("/mcp", data="{}", content_type="application/json").status_code, 401)
        answer = self.rpc(self.ana, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
        self.assertEqual((answer["protocolVersion"], answer["serverInfo"]["name"]), ("2025-03-26", "QopenCRM"))
        self.assertEqual(self.rpc(self.ana, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]["protocolVersion"], "2025-06-18")
        notification = self.client.post("/mcp/", data=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                                        content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.keys[self.ana]}")
        self.assertEqual(notification.status_code, 202)
        self.assertEqual(self.rpc(self.ana, "nope").json()["error"]["code"], -32601)

    def test_tools_follow_role(self):
        names = lambda user: {t["name"] for t in self.rpc(user, "tools/list").json()["result"]["tools"]}  # noqa: E731
        ana, boss = names(self.ana), names(self.boss)
        self.assertIn("create_opportunity", ana)
        self.assertFalse({"get_report", "create_product", "erase_contact"} & ana)
        self.assertTrue({"get_report", "create_product", "erase_contact"} <= boss)
        role = self.ana.role
        role.mcp_tools = ["whoami", "list_opportunities", "get_report"]
        role.save()
        self.assertEqual(names(self.ana), {"whoami", "list_opportunities"})  # la lista recorta, nunca amplía permisos
        self.assertTrue(self.call(self.ana, "create_task", title="x", due_at="mañana")[0])
        role.can_use_api = False
        role.save()
        self.assertEqual(self.rpc(self.ana, "tools/list").status_code, 403)

    def test_reads_are_scoped(self):
        _, mine = self.call(self.ana, "list_opportunities")
        self.assertEqual([o["title"] for o in mine["opportunities"]], ["Mía"])
        _, everything = self.call(self.boss, "list_opportunities")
        self.assertEqual(everything["count"], 2)
        error, message = self.call(self.ana, "get_opportunity", opportunity_id=self.theirs.pk)
        self.assertTrue(error)
        self.assertIn("no tienes acceso", message)
        self.assertEqual(self.call(self.ana, "search_companies", q="redes")[1], [])

    def test_commercial_flow_by_assistant(self):
        from apps.crm.models import Opportunity, Task

        error, opp = self.call(self.ana, "create_opportunity", title="Lead por asistente", company_name="FIBRA ACME SL", source="Feria")
        self.assertFalse(error, opp)
        self.assertEqual(opp["company_id"], self.acme.pk)  # reutiliza la empresa, no la duplica
        error, line = self.call(self.ana, "add_line", opportunity_id=opp["id"], description="Cuota a medida", billing_model="monthly", unit_price=300)
        self.assertEqual((error, line["opportunity"]["mrr"]), (False, 300.0))
        error, task = self.call(self.ana, "create_task", title="Llamar", due_at="+3d", opportunity_id=opp["id"])
        self.assertFalse(error, task)
        self.assertIsNotNone(Opportunity.objects.get(pk=opp["id"]).next_action_at)
        self.assertFalse(self.call(self.ana, "log_activity", kind="call", body="Quiere demo", opportunity_id=opp["id"])[0])
        error, message = self.call(self.ana, "move_opportunity", opportunity_id=opp["id"], stage="Perdida")
        self.assertTrue(error)
        self.assertIn("loss_reason", message)
        error, moved = self.call(self.ana, "move_opportunity", opportunity_id=opp["id"], stage="Ganada")
        self.assertEqual((error, moved["stage"]), (False, "Ganada"))
        self.assertEqual(self.call(self.ana, "list_subscriptions")[1]["subscriptions"][0]["plan"], "Cuota a medida")
        self.assertTrue(self.call(self.ana, "complete_task", task_id=task["id"])[1]["done"])
        self.assertTrue(Task.objects.get(pk=task["id"]).done_at)
        self.assertTrue(self.call(self.ana, "create_task", title="x", due_at="cuando sea")[0])
        self.assertTrue(self.call(self.ana, "whoami", sobra=1)[0])  # parámetros desconocidos se rechazan

    def test_writes_respect_ownership(self):
        error, message = self.call(self.boss, "update_opportunity", opportunity_id=self.theirs.pk, priority=3)
        self.assertFalse(error, message)
        self.theirs.assignees.add(self.ana)  # ahora la ve, y al estar asignada puede editarla
        self.assertFalse(self.call(self.ana, "update_opportunity", opportunity_id=self.theirs.pk, source="Web")[0])
        error, message = self.call(self.ana, "update_opportunity", opportunity_id=self.theirs.pk, owner="jefe@example.com")
        self.assertTrue(error)
        self.assertIn("responsable", message)


@override_settings(SITE_URL="https://crm.example.com", EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class OAuthTests(TestCase):
    """Servidor de autorización del MCP: descubrimiento, alta de cliente, consentimiento, PKCE y rotación."""

    REDIRECT = "https://claude.ai/api/mcp/auth_callback"
    VERIFIER = "v" * 64

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from apps.accounts.models import Role

        call_command("seed", verbosity=0)
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))

    @property
    def challenge(self):
        import base64
        import hashlib

        return base64.urlsafe_b64encode(hashlib.sha256(self.VERIFIER.encode()).digest()).rstrip(b"=").decode()

    def register(self, **extra):
        return self.client.post("/mcp/oauth/register", data=json.dumps({"client_name": "Claude", "redirect_uris": [self.REDIRECT], **extra}),
                                content_type="application/json")

    def params(self, client_id, **changes):
        return {"client_id": client_id, "redirect_uri": self.REDIRECT, "response_type": "code", "state": "abc",
                "code_challenge": self.challenge, "code_challenge_method": "S256", "resource": "https://crm.example.com/mcp/", **changes}

    def authorize(self, client_id):
        from urllib.parse import parse_qs, urlparse

        self.client.force_login(self.ana)
        response = self.client.post("/mcp/oauth/authorize", {**self.params(client_id), "decision": "allow"})
        return parse_qs(urlparse(response["Location"]).query)

    def exchange(self, client_id, code, **changes):
        return self.client.post("/mcp/oauth/token", {"grant_type": "authorization_code", "client_id": client_id, "code": code,
                                                     "redirect_uri": self.REDIRECT, "code_verifier": self.VERIFIER, **changes})

    def test_discovery_points_to_this_server(self):
        unauthorized = self.client.post("/mcp/", data="{}", content_type="application/json")
        self.assertIn('resource_metadata="https://crm.example.com/.well-known/oauth-protected-resource"', unauthorized["WWW-Authenticate"])
        resource = self.client.get("/.well-known/oauth-protected-resource/mcp/").json()
        self.assertEqual((resource["resource"], resource["authorization_servers"]), ("https://crm.example.com/mcp/", ["https://crm.example.com"]))
        server = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(server["code_challenge_methods_supported"], ["S256"])
        self.assertEqual(server["token_endpoint"], "https://crm.example.com/mcp/oauth/token")

    def test_full_flow_then_mcp_with_user_permissions(self):
        client_id = self.register().json()["client_id"]
        # sin sesión: al login, y el login recuerda adónde volver
        from urllib.parse import urlencode

        anonymous = self.client.get("/mcp/oauth/authorize?" + urlencode(self.params(client_id)))
        self.assertIn("/entrar/", anonymous["Location"])
        self.client.get(anonymous["Location"])
        self.assertIn("/mcp/oauth/authorize", self.client.session["login_next"])
        self.client.force_login(self.ana)
        consent = self.client.get("/mcp/oauth/authorize?" + urlencode(self.params(client_id)))
        self.assertContains(consent, "Claude quiere acceder")
        answer = self.authorize(client_id)
        self.assertEqual((answer["state"], answer["iss"]), (["abc"], ["https://crm.example.com"]))
        tokens = self.exchange(client_id, answer["code"][0]).json()
        self.assertEqual((tokens["token_type"], tokens["expires_in"]), ("Bearer", 3600))
        self.client.logout()
        call = self.client.post("/mcp/", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "whoami", "arguments": {}}}),
                                content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
        self.assertEqual(json.loads(call.json()["result"]["content"][0]["text"])["email"], "ana@example.com")
        # solo se guardan hashes
        from apps.integrations.models import OAuthToken

        self.assertFalse(OAuthToken.objects.filter(access_hash=tokens["access_token"]).exists())

    def test_code_is_single_use_and_bound(self):
        client_id = self.register().json()["client_id"]
        code = self.authorize(client_id)["code"][0]
        self.assertEqual(self.exchange(client_id, code, code_verifier="x" * 64).json()["error"], "invalid_grant")  # PKCE
        self.assertEqual(self.exchange(client_id, code, redirect_uri="https://evil.example/cb").json()["error"], "invalid_grant")
        first = self.exchange(client_id, code).json()
        self.assertIn("access_token", first)
        self.assertEqual(self.exchange(client_id, code).json()["error"], "invalid_grant")
        # reutilizar el código anula lo emitido con él
        stale = self.client.post("/mcp/", data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {first['access_token']}")
        self.assertEqual(stale.status_code, 401)

    def test_refresh_rotates_and_reuse_revokes(self):
        client_id = self.register().json()["client_id"]
        first = self.exchange(client_id, self.authorize(client_id)["code"][0]).json()
        refresh = lambda token: self.client.post("/mcp/oauth/token", {"grant_type": "refresh_token", "client_id": client_id, "refresh_token": token})  # noqa: E731
        second = refresh(first["refresh_token"]).json()
        self.assertNotEqual(second["refresh_token"], first["refresh_token"])
        self.assertEqual(refresh(first["refresh_token"]).json()["error"], "invalid_grant")
        blocked = self.client.post("/mcp/", data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {second['access_token']}")
        self.assertEqual(blocked.status_code, 401)

    def test_rejects_bad_requests(self):
        self.assertEqual(self.register(redirect_uris=["http://evil.example/cb"]).status_code, 400)
        self.assertEqual(self.register(redirect_uris=["http://127.0.0.1:8123/cb"]).status_code, 201)
        client_id = self.register().json()["client_id"]
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get("/mcp/oauth/authorize", self.params(client_id, redirect_uri="https://evil.example/cb")).status_code, 400)
        self.assertEqual(self.client.get("/mcp/oauth/authorize", self.params("nope")).status_code, 400)
        plain = self.client.get("/mcp/oauth/authorize", self.params(client_id, code_challenge_method="plain"))
        self.assertIn("error=invalid_request", plain["Location"])
        denied = self.client.post("/mcp/oauth/authorize", {**self.params(client_id), "decision": "deny"})
        self.assertIn("error=access_denied", denied["Location"])
        other = self.client.get("/mcp/oauth/authorize", self.params(client_id, resource="https://otro.example/mcp/"))
        self.assertIn("error=invalid_target", other["Location"])

    def test_user_can_revoke_an_app(self):
        from apps.integrations.models import OAuthClient

        client_id = self.register().json()["client_id"]
        tokens = self.exchange(client_id, self.authorize(client_id)["code"][0]).json()
        self.assertContains(self.client.get("/ajustes/"), "Retirar acceso")
        self.client.post(f"/mcp/oauth/apps/{OAuthClient.objects.get(client_id=client_id).pk}/revocar/")
        gone = self.client.post("/mcp/", data="{}", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {tokens['access_token']}")
        self.assertEqual(gone.status_code, 401)


@override_settings(SITE_URL="https://crm.example.com")
class TeamsTests(TestCase):
    """Reuniones de Teams: configuración cifrada, conexión por OAuth de Microsoft y convocatoria (Graph simulado)."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from apps.accounts.models import Role

        call_command("seed", verbosity=0)
        cls.boss = User.objects.create_superuser("jefe@example.com", name="Jefa")
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))
        cls.luis = User.objects.create_user("luis@example.com", name="Luis Ventas", role=Role.objects.get(code="comercial"))
        company = Company.objects.create(name="Fibra Acme", owner=cls.ana)
        contact = Contact.objects.create(company=company, first_name="Marta", email="marta@acme.es")
        cls.opp = Opportunity.objects.create(title="Demo", company=company, contact=contact, stage=Stage.objects.first(), owner=cls.ana)

    def configure(self):
        self.client.force_login(self.boss)
        self.client.post("/ajustes/microsoft/", {"tenant_id": "tenant-1", "client_id": "client-1", "client_secret": "s3cr3t-valor"})

    def fake_graph(self, calls):
        from unittest import mock

        def request(url, data=None, token=None, json_body=None):
            calls.append({"url": url, "data": data, "token": token, "json": json_body})
            if url.endswith("/token"):
                return {"access_token": "access-1", "refresh_token": "refresh-2"}
            if "/me?" in url:
                return {"displayName": "Info Acme", "mail": "Info@Example.onmicrosoft.com"}
            return {"id": "evento-1", "webLink": "https://outlook/evento", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/abc"}}

        return mock.patch("apps.integrations.microsoft._request", side_effect=request)

    def test_config_is_superuser_only_and_secret_is_encrypted(self):
        from apps.integrations import microsoft
        from apps.integrations.models import MicrosoftConfig

        self.client.force_login(self.ana)
        self.assertEqual(self.client.post("/ajustes/microsoft/", {"tenant_id": "t", "client_id": "c", "client_secret": "x"}).status_code, 403)
        self.configure()
        config = MicrosoftConfig.load()
        self.assertTrue(config.ready)
        self.assertNotIn("s3cr3t-valor", config.client_secret_enc)
        self.assertEqual(microsoft.decrypt(config.client_secret_enc), "s3cr3t-valor")
        self.assertNotContains(self.client.get("/ajustes/"), "s3cr3t-valor")
        self.client.post("/ajustes/microsoft/", {"tenant_id": "tenant-2", "client_id": "client-1", "client_secret": ""})  # vacío conserva el secreto
        config.refresh_from_db()
        self.assertEqual((config.tenant_id, microsoft.decrypt(config.client_secret_enc)), ("tenant-2", "s3cr3t-valor"))

    def test_connect_goes_through_microsoft_and_checks_state(self):
        from apps.integrations.models import MicrosoftAccount

        self.client.force_login(self.ana)
        self.assertRedirects(self.client.get("/ajustes/microsoft/conectar/"), "/ajustes/#teams", fetch_redirect_response=False)  # sin configurar
        self.configure()
        going = self.client.get("/ajustes/microsoft/conectar/?shared=1")
        self.assertIn("https://login.microsoftonline.com/tenant-1/oauth2/v2.0/authorize", going["Location"])
        self.assertIn("redirect_uri=https%3A%2F%2Fcrm.example.com%2Fajustes%2Fmicrosoft%2Fcallback%2F", going["Location"])
        state = self.client.session["ms_oauth"]["state"]
        calls = []
        with self.fake_graph(calls):
            self.client.get("/ajustes/microsoft/callback/", {"code": "c0d3", "state": "otro"})
            self.assertFalse(MicrosoftAccount.objects.exists())  # estado distinto: no se canjea nada
            self.assertEqual(calls, [])
            self.client.get("/ajustes/microsoft/conectar/?shared=1")
            self.client.get("/ajustes/microsoft/callback/", {"code": "c0d3", "state": self.client.session["ms_oauth"]["state"]})
        account = MicrosoftAccount.objects.get()
        self.assertEqual((account.email, account.shared, account.owner), ("info@example.onmicrosoft.com", True, self.boss))
        self.assertNotIn("refresh-2", account.refresh_token_enc)
        self.assertNotEqual(state, "")

    def test_meeting_creates_event_activity_and_next_action(self):
        from apps.crm.models import Task
        from apps.integrations import microsoft
        from apps.integrations.models import MicrosoftAccount

        self.configure()
        shared = MicrosoftAccount.objects.create(owner=self.boss, email="info@example.onmicrosoft.com", shared=True, refresh_token_enc=microsoft.encrypt("refresh-1"))
        private = MicrosoftAccount.objects.create(owner=self.luis, email="luis@example.com", refresh_token_enc=microsoft.encrypt("r"))
        self.client.force_login(self.ana)
        form = self.client.get(f"/oportunidades/{self.opp.pk}/teams/")
        self.assertContains(form, "info@example.onmicrosoft.com")
        self.assertNotContains(form, "luis@example.com")  # la cuenta personal de otro no se ofrece
        self.assertContains(form, "marta@acme.es")
        calls = []
        data = {"account": shared.pk, "subject": "Demo Atlas", "start": "2030-05-06T10:00", "minutes": "60", "attendees": "marta@acme.es, otro@acme.es", "message": "Nos vemos"}
        with self.fake_graph(calls):
            self.assertEqual(self.client.post(f"/oportunidades/{self.opp.pk}/teams/", {**data, "account": private.pk}).status_code, 200)  # cuenta ajena: rechazada
            self.assertEqual(calls, [])
            self.assertEqual(self.client.post(f"/oportunidades/{self.opp.pk}/teams/", data).status_code, 204)
        event = calls[-1]["json"]
        self.assertEqual((event["isOnlineMeeting"], event["start"], event["end"]["dateTime"]),
                         (True, {"dateTime": "2030-05-06T10:00:00", "timeZone": "Europe/Madrid"}, "2030-05-06T11:00:00"))
        self.assertEqual([a["emailAddress"]["address"] for a in event["attendees"]], ["marta@acme.es", "otro@acme.es"])
        activity = Activity.objects.get(source="teams")
        self.assertIn("https://teams.microsoft.com/l/meetup-join/abc", activity.body)
        task = Task.objects.get(opportunity=self.opp)
        self.assertEqual((task.kind, task.notes), ("meeting", "https://teams.microsoft.com/l/meetup-join/abc"))
        shared.refresh_from_db()
        self.assertEqual(microsoft.decrypt(shared.refresh_token_enc), "refresh-2")  # Microsoft rota el token: se guarda el nuevo
        self.client.force_login(self.luis)  # quien no lleva la oportunidad no puede convocar desde ella
        self.assertEqual(self.client.get(f"/oportunidades/{self.opp.pk}/teams/").status_code, 404)


@override_settings(SITE_URL="https://crm.example.com")
class TeamsAppMailboxTests(TestCase):
    """Buzones dados de alta con permisos de aplicación: sin inicio de sesión de nadie."""

    @classmethod
    def setUpTestData(cls):
        from django.core.management import call_command

        from apps.accounts.models import Role

        call_command("seed", verbosity=0)
        cls.boss = User.objects.create_superuser("jefe@example.com", name="Jefa")
        cls.ana = User.objects.create_user("ana@example.com", name="Ana Ventas", role=Role.objects.get(code="comercial"))
        company = Company.objects.create(name="Fibra Acme", owner=cls.ana)
        cls.opp = Opportunity.objects.create(title="Demo", company=company, stage=Stage.objects.first(), owner=cls.ana)

    def test_mailbox_needs_application_permission(self):
        import base64
        from unittest import mock

        from apps.integrations.models import MicrosoftAccount

        self.client.force_login(self.boss)
        self.client.post("/ajustes/microsoft/", {"tenant_id": "t", "client_id": "c", "client_secret": "s"})
        claims = base64.urlsafe_b64encode(json.dumps({"app_displayname": "CRM"}).encode()).decode().rstrip("=")
        with mock.patch("apps.integrations.microsoft._request", return_value={"access_token": f"x.{claims}.y"}) as request:
            page = self.client.post("/ajustes/microsoft/buzon/", {"email": "info@example.onmicrosoft.com", "owner": ""}, follow=True)
        self.assertContains(page, "no tiene el permiso de aplicación «Calendars.ReadWrite»")
        self.assertContains(page, "Conectar mi cuenta")
        self.assertEqual(request.call_count, 1)  # no llega a preguntar a Graph por el calendario
        self.assertFalse(MicrosoftAccount.objects.exists())

    def test_add_mailbox_and_convene_with_app_token(self):
        from unittest import mock

        from apps.integrations.models import MicrosoftAccount

        self.client.force_login(self.boss)
        self.client.post("/ajustes/microsoft/", {"tenant_id": "t", "client_id": "c", "client_secret": "s"})
        calls = []

        def request(url, data=None, token=None, json_body=None):
            calls.append({"url": url, "data": data, "token": token, "json": json_body})
            if url.endswith("/token"):
                return {"access_token": "app-token"}
            if "/calendar?" in url:
                return {"name": "Calendario"}
            return {"id": "ev", "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/meetup-join/x"}}

        with mock.patch("apps.integrations.microsoft._request", side_effect=request):
            self.client.post("/ajustes/microsoft/buzon/", {"email": "Info@Example.onmicrosoft.com", "owner": ""})
            account = MicrosoftAccount.objects.get()
            self.assertEqual((account.email, account.shared, account.refresh_token_enc), ("info@example.onmicrosoft.com", True, ""))
            self.assertEqual(calls[0]["data"]["grant_type"], "client_credentials")
            self.client.force_login(self.ana)
            self.assertEqual(self.client.post("/ajustes/microsoft/buzon/", {"email": "x@y.es"}).status_code, 403)
            response = self.client.post(f"/oportunidades/{self.opp.pk}/teams/", {"account": account.pk, "subject": "Demo", "start": "2030-05-06T10:00",
                                                                                "minutes": "30", "attendees": "marta@acme.es"})
        self.assertEqual(response.status_code, 204)
        self.assertEqual(calls[-1]["url"], "https://graph.microsoft.com/v1.0/users/info%40example.onmicrosoft.com/events")
        self.assertEqual(calls[-1]["token"], "app-token")
