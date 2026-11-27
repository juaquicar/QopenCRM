"""Formularios de la web. Cada uno se abre en un diálogo (ver views.modal_form)."""
import re

from django import forms
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.permissions import scope
from apps.catalog.models import Product, ProductFamily
from apps.integrations.models import Webhook
from apps.sales.models import HourBank, HourEntry, Nda, QuoteTemplate, Subscription

from .models import Activity, Company, Contact, Event, LossReason, Opportunity, OpportunityLine, Stage, Task, normalize_name


class ComboWidget(forms.Widget):
    """Desplegable con buscador para empresas y contactos: no carga la lista entera, busca al escribir."""

    def __init__(self, kind, queryset, create="", depends="", placeholder="Escribe para buscar…"):
        super().__init__()
        self.kind, self.queryset, self.create, self.depends, self.placeholder = kind, queryset, create, depends, placeholder

    def render(self, name, value, attrs=None, renderer=None):
        from django.urls import reverse
        from django.utils.html import format_html

        current = self.queryset.filter(pk=value).first() if str(value or "").isdigit() else None
        return format_html(
            '<div class="combo" data-combo data-url="{}" data-create="{}" data-depends="{}">'
            '<input type="hidden" name="{}" value="{}">'
            '<input type="text" class="combo-input" id="{}" value="{}" placeholder="{}" autocomplete="off" role="combobox" aria-expanded="false" aria-autocomplete="list">'
            '<ul class="combo-list" role="listbox" hidden></ul></div>',
            reverse("lookup", args=[self.kind]), self.create, self.depends, name, current.pk if current else "",
            (attrs or {}).get("id", f"id_{name}"), str(current) if current else "", self.placeholder)


class MultiComboWidget(forms.Widget):
    """Varias empresas con buscador: cada una elegida queda como una etiqueta con su ✕."""

    def __init__(self, kind, queryset, placeholder="Escribe para añadir otra empresa…"):
        super().__init__()
        self.kind, self.queryset, self.placeholder = kind, queryset, placeholder

    def value_from_datadict(self, data, files, name):
        return data.getlist(name) if hasattr(data, "getlist") else data.get(name, [])

    def render(self, name, value, attrs=None, renderer=None):
        from django.urls import reverse
        from django.utils.html import format_html, format_html_join

        ids = [str(v.pk if hasattr(v, "pk") else v) for v in (value or []) if str(getattr(v, "pk", v)).isdigit()]
        chosen = self.queryset.model.objects.filter(pk__in=ids) if ids else []
        chips = format_html_join("", '<span class="chip-pick">{}<input type="hidden" name="{}" value="{}"><button type="button" aria-label="Quitar">✕</button></span>',
                                 ((str(c), name, c.pk) for c in chosen))
        return format_html(
            '<div class="combo multi" data-combo data-multi="{}" data-url="{}"><div class="chips">{}</div>'
            '<input type="hidden" value=""><input type="text" class="combo-input" id="{}" placeholder="{}" autocomplete="off" role="combobox" aria-expanded="false" aria-autocomplete="list">'
            '<ul class="combo-list" role="listbox" hidden></ul></div>',
            name, reverse("lookup", args=[self.kind]), chips, (attrs or {}).get("id", f"id_{name}"), self.placeholder)


def _lines(text):
    return [line.strip(" ,;") for line in (text or "").replace(",", "\n").replace(";", "\n").splitlines() if line.strip(" ,;")]


class BaseForm(forms.ModelForm):
    """Recibe el usuario para acotar los desplegables a lo que puede ver."""

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.ModelChoiceField) and user is not None:
                model = field.queryset.model
                if model in (Company, Contact, Opportunity):
                    field.queryset = scope(field.queryset, user)
                if model is User:
                    field.queryset = field.queryset.filter(is_active=True)
                if model in (Company, Contact) and not isinstance(field, forms.ModelMultipleChoiceField):
                    field.widget = ComboWidget("company" if model is Company else "contact", field.queryset,
                                               depends="company" if model is Contact and "company" in self.fields else "")
            if isinstance(field, forms.ModelChoiceField) and not isinstance(field, forms.ModelMultipleChoiceField):
                field.empty_label = "—"
            if isinstance(field.widget, forms.Textarea):
                field.widget.attrs["rows"] = 3
            _mobile_keyboard(name, field)
            if isinstance(field, forms.DateTimeField):
                field.widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
                field.input_formats = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]
            elif isinstance(field, forms.DateField):
                field.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


def _mobile_keyboard(name, field):
    """En el móvil, cada campo abre su teclado: numérico para teléfonos, mayúscula inicial en los nombres."""
    if isinstance(field.widget, forms.Textarea):
        return
    if name.endswith(("phone", "mobile")):
        field.widget.attrs.update({"inputmode": "tel", "autocomplete": "off"})
    elif name.endswith(("first_name", "last_name")) or name == "name":
        field.widget.attrs.update({"autocapitalize": "words", "autocomplete": "off"})
    elif name.endswith("website"):
        field.widget.attrs.update({"inputmode": "url", "autocapitalize": "none"})


def _insert_after(form, anchor, new_fields):
    """Coloca campos añadidos en __init__ justo detrás de otro (los de la empresa o el contacto nuevo, juntos)."""
    items = [(k, v) for k, v in form.fields.items() if k not in new_fields]
    at = [k for k, _ in items].index(anchor) + 1
    form.fields = dict(items[:at] + list(new_fields.items()) + items[at:])


def _model_field(model, name, prefix):
    field = model._meta.get_field(name).formfield()
    field.required = False
    field.label = str(field.label)[:1].upper() + str(field.label)[1:]
    if isinstance(field.widget, forms.Textarea):
        field.widget.attrs["rows"] = 2
    _mobile_keyboard(name, field)
    return f"{prefix}{name}", field


class NewCompanyMixin(forms.Form):
    """Empresa nueva desde el propio diálogo: el buscador ofrece «＋ Crear empresa» y se da de alta al guardar,
    con sus datos principales (los mismos que en la ficha de empresa)."""

    COMPANY_FIELDS = ["kind", "tax_id", "sector", "website", "email", "phone", "address", "city", "province", "country"]

    new_company = forms.CharField(required=False, max_length=200, widget=forms.HiddenInput)
    new_company_event = forms.ModelChoiceField(label="Conocida en (feria o evento)", queryset=Event.objects.all(), required=False,
                                               help_text="Dónde conociste a la empresa nueva. Déjalo vacío si no viene de ninguna feria o evento.")

    def setup_new_company(self):
        self.fields["company"].widget.create = "new_company"
        _insert_after(self, "new_company", dict(_model_field(Company, name, "new_company_") for name in self.COMPANY_FIELDS))
        self.fields["new_company_event"].empty_label = "—"

    def clean_new_company(self):
        return (self.cleaned_data.get("new_company") or "").strip()

    def resolve_company(self):
        """La empresa elegida o, si se escribió una nueva, la recién creada (o la que ya había con ese nombre o CIF)."""
        data = self.cleaned_data
        if data.get("company") or not data.get("new_company"):
            return data.get("company")
        values = {name: data.get(f"new_company_{name}") for name in self.COMPANY_FIELDS}
        values = {k: v for k, v in values.items() if v not in (None, "")}
        values["event"] = data.get("new_company_event")
        key = normalize_name(data["new_company"])
        tax_id = re.sub(r"[\s.\-]", "", values.get("tax_id", "")).upper()
        # misma empresa escrita otra vez (nombre o CIF): se usa la que ya hay, no se duplica, y se completa lo que le falte
        visible = scope(Company.objects.all(), self.user)
        existing = (visible.filter(name_key=key).first() if key else None) or (visible.filter(tax_id=tax_id).first() if tax_id else None)
        if existing:
            missing = [k for k, v in values.items() if v and not getattr(existing, k) and k != "kind"]
            for k in missing:
                setattr(existing, k, values[k])
            if missing:
                existing.save()
            self.created_company = None
            return existing
        company = Company.objects.create(name=data["new_company"], owner=self.user, **values)
        self.created_company = company
        return company


class CompanyForm(BaseForm):
    class Meta:
        model = Company
        fields = ["name", "kind", "tax_id", "website", "email", "phone", "sector", "address", "city", "province", "country", "owner", "event", "notes"]


class EventForm(BaseForm):
    class Meta:
        model = Event
        fields = ["name", "kind", "starts_on", "ends_on", "place", "website", "notes"]

    def clean(self):
        data = super().clean()
        if data.get("starts_on") and data.get("ends_on") and data["ends_on"] < data["starts_on"]:
            self.add_error("ends_on", "No puede terminar antes de empezar.")
        return data


class ContactForm(NewCompanyMixin, BaseForm):
    """Alta de contacto: su empresa se busca o se crea ahí mismo, igual que al dar de alta una oportunidad.
    Puede ser de varias empresas y tener varios correos y teléfonos."""

    extra_emails = forms.CharField(label="Otros correos", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                   help_text="Uno por línea. El correo de arriba es el principal.")
    extra_phones = forms.CharField(label="Otros teléfonos", required=False, widget=forms.Textarea(attrs={"rows": 2}),
                                   help_text="Uno por línea, con una nota si quieres: «91 000 00 00 centralita».")

    class Meta:
        model = Contact
        fields = ["first_name", "last_name", "company", "other_companies", "position", "email", "extra_emails", "mobile", "phone", "extra_phones",
                  "is_primary", "owner", "legal_basis", "consent_marketing", "consent_source", "notes"]

    field_order = ["first_name", "last_name", "company", "new_company", "new_company_event", "other_companies", "position",
                   "email", "extra_emails", "mobile", "phone", "extra_phones"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_new_company()
        self.fields["company"].label = "Empresa principal"
        self.fields["company"].help_text = "Si aún no está, escribe su nombre y créala aquí."
        self.fields["other_companies"].widget = MultiComboWidget("company", self.fields["other_companies"].queryset)
        self.fields["other_companies"].help_text = "Si también trabaja con otras empresas: aparecerá en la ficha de cada una."
        self.fields["email"].label, self.fields["mobile"].label, self.fields["phone"].label = "Correo principal", "Móvil", "Teléfono"
        if self.instance.pk:
            self.fields["extra_emails"].initial = "\n".join(self.instance.values("email")[1 if self.instance.email else 0:])
            phones = [p for p in self.instance.values("phone") if p not in (self.instance.mobile, self.instance.phone)]
            self.fields["extra_phones"].initial = "\n".join(phones)

    def clean_extra_emails(self):
        emails = [e.lower() for e in _lines(self.cleaned_data.get("extra_emails"))]
        bad = []
        for e in emails:
            try:
                forms.EmailField().clean(e)
            except forms.ValidationError:
                bad.append(e)
        if bad:
            raise forms.ValidationError(f"No parece un correo: {', '.join(bad)}")
        return emails

    def clean_extra_phones(self):
        return [line.strip() for line in (self.cleaned_data.get("extra_phones") or "").splitlines() if line.strip()]

    def clean(self):
        data = super().clean()
        if data.get("company") and data.get("other_companies"):
            data["other_companies"] = [c for c in data["other_companies"] if c.pk != data["company"].pk]
        return data

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.set_channels("email", self.cleaned_data.get("extra_emails") or [])
        self.instance.set_channels("phone", self.cleaned_data.get("extra_phones") or [])

    def save(self, commit=True):
        if "consent_marketing" in self.changed_data and self.instance.consent_marketing:
            self.instance.consent_at = timezone.now()
        self.instance.company = self.resolve_company()
        return super().save(commit)


class OpportunityForm(NewCompanyMixin, BaseForm):
    """Alta de oportunidad sin salir del diálogo: la empresa y el contacto se buscan o se crean ahí mismo."""

    CONTACT_FIELDS = ["first_name", "last_name", "position", "email", "mobile", "phone", "legal_basis"]

    class Meta:
        model = Opportunity
        fields = ["title", "company", "contact", "stage", "owner", "assignees", "priority", "probability", "expected_close", "source", "description"]
        widgets = {"assignees": forms.CheckboxSelectMultiple}

    field_order = ["title", "company", "new_company", "new_company_event", "contact"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["stage"].queryset = Stage.objects.filter(is_won=False, is_lost=False)
        self.fields["assignees"].queryset = User.objects.filter(is_active=True)
        self.fields["company"].required = False  # puede llegar como empresa nueva
        self.fields["company"].mandatory = True  # pero una de las dos hace falta: no se rotula como opcional
        self.setup_new_company()
        self.fields["company"].help_text = "La empresa u organismo cliente. Si aún no está, escribe su nombre y créala aquí."
        self.fields["contact"].widget.create = "new_contact"
        _insert_after(self, "contact", dict(_model_field(Contact, name, "new_contact_") for name in self.CONTACT_FIELDS))
        self.fields["new_contact_first_name"].label = "Nombre del contacto"
        self.fields["new_contact_legal_basis"].initial = Contact._meta.get_field("legal_basis").default
        self.fields["new_contact_legal_basis"].help_text = "RGPD: por qué puedes guardar y usar sus datos."
        self.fields["contact"].help_text = "La persona con la que hablas. Búscala o créala aquí."
        if not self.instance.pk:
            del self.fields["probability"]

    def clean(self):
        data = super().clean()
        if not data.get("company") and not data.get("new_company") and "company" not in self.errors:
            self.add_error("company", "Elige una empresa de la lista o crea una nueva.")
        contact = data.get("contact")
        if contact and data.get("company") and contact.company_id and not contact.works_at(data["company"]):
            self.add_error("contact", f"{contact} es de otra empresa ({contact.company}). Si también trabaja con esta, añádela en su ficha.")
        if any(data.get(f"new_contact_{name}") for name in ("last_name", "position", "email", "mobile", "phone")) and not data.get("new_contact_first_name"):
            self.add_error("new_contact_first_name", "Indica al menos el nombre del contacto nuevo.")
        return data

    def save(self, commit=True):
        data, opportunity = self.cleaned_data, self.instance
        opportunity.company = self.resolve_company()
        if not data.get("contact") and data.get("new_contact_first_name"):
            email = (data.get("new_contact_email") or "").lower()
            existing = Contact.objects.filter(company=opportunity.company, email__iexact=email).first() if email else None
            values = {name: data.get(f"new_contact_{name}") or "" for name in self.CONTACT_FIELDS}
            values.update(email=email, legal_basis=values["legal_basis"] or Contact._meta.get_field("legal_basis").default)
            opportunity.contact = existing or Contact.objects.create(
                company=opportunity.company, owner=self.user, is_primary=not opportunity.company.contacts.exists(), **values,
            )
            self.created_contact = None if existing else opportunity.contact
        return super().save(commit)


class LineForm(BaseForm):
    """Línea de oportunidad: un producto del catálogo o una línea libre recurrente, por horas o de pago único."""

    class Meta:
        model = OpportunityLine
        fields = ["product", "billing_model", "description", "quantity", "unit", "unit_price", "discount", "period"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.exclude(status=Product.Status.RETIRED)
        self.fields["product"].required = False
        self.fields["product"].empty_label = "Sin producto: línea libre"
        self.fields["product"].mandatory = True
        self.fields["product"].help_text = "Elige uno del catálogo o deja «línea libre» y descríbela tú."
        self.fields["billing_model"].label = "Tipo de cobro"
        self.fields["billing_model"].required = False  # con producto, lo pone el producto
        self.fields["billing_model"].mandatory = True
        self.fields["billing_model"].help_text = "Recurrente suma al MRR; por horas, a servicios; pago único, aparte."
        self.fields["description"].help_text = "Obligatorio en una línea libre."
        self.fields["unit_price"].help_text = "Vacío = precio a consultar"
        self.fields["period"].help_text = "Solo para líneas recurrentes."

    def clean(self):
        data = super().clean()
        product = data.get("product")
        if not product and not (data.get("description") or "").strip():
            self.add_error("description", "Describe la línea o elige un producto del catálogo.")
        if not data.get("billing_model"):
            if product:
                data["billing_model"] = product.billing_model
            else:
                self.add_error("billing_model", "Elige el tipo de cobro: recurrente, por horas o pago único.")
        return data

    def save(self, commit=True):
        line = super().save(commit=False)
        if not line.unit:
            line.unit = line.product.unit if line.product_id else {"monthly": "mes", "hourly": "hora"}.get(line.billing_model, "")
        if commit:
            line.save()
        return line


# textarea con sugerencias al escribir «@» (ver app.js)
MENTIONS = {"data-mentions": "/buscar/user/", "placeholder": "Escribe @ para mencionar a alguien del equipo"}


class TaskForm(BaseForm):
    class Meta:
        model = Task
        fields = ["title", "kind", "due_at", "assignee", "assignees", "opportunity", "company", "notes"]
        widgets = {"assignees": forms.CheckboxSelectMultiple}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["notes"].widget.attrs.update(MENTIONS)
        self.fields["assignees"].queryset = User.objects.filter(is_active=True)

    def clean_assignees(self):
        main = self.cleaned_data.get("assignee")
        return [u for u in self.cleaned_data.get("assignees", []) if u != main]


class ActivityForm(BaseForm):
    class Meta:
        model = Activity
        fields = ["kind", "subject", "body", "occurred_at", "contact", "opportunity"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"].choices = [c for c in Activity.Kind.choices if c[0] in ("note", "call", "email", "meeting")]
        self.fields["body"].widget.attrs.update(MENTIONS)


class LoseForm(forms.Form):
    loss_reason = forms.ModelChoiceField(label="Motivo de pérdida", queryset=LossReason.objects.all(), empty_label="Elige un motivo…")
    loss_notes = forms.CharField(label="Detalle", required=False, max_length=300)


class HoldForm(forms.Form):
    """Parar una oportunidad a la espera del cliente: fecha de revisión obligatoria, con tope."""

    until = forms.DateField(label="Revisar el", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                            help_text=f"Obligatoria. Como máximo, {Opportunity.MAX_HOLD_DAYS} días desde hoy.")
    reason = forms.CharField(label="Por qué está parada", max_length=200,
                             help_text="Ej.: esperan presupuesto del año que viene, cambio de responsable, pendiente de su comité.")

    def clean_until(self):
        until, today = self.cleaned_data["until"], timezone.localdate()
        if until <= today:
            raise forms.ValidationError("Tiene que ser posterior a hoy.")
        if (until - today).days > Opportunity.MAX_HOLD_DAYS:
            raise forms.ValidationError(f"Demasiado lejos: como máximo {Opportunity.MAX_HOLD_DAYS} días.")
        return until


class ProductForm(BaseForm):
    class Meta:
        model = Product
        fields = ["family", "code", "name", "description", "billing_model", "unit", "price", "price_annual", "tier_level", "is_addon", "status"]


class FamilyForm(BaseForm):
    class Meta:
        model = ProductFamily
        fields = ["name", "order", "color"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class StageForm(BaseForm):
    class Meta:
        model = Stage
        fields = ["name", "order", "probability", "color", "is_won", "is_lost"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}


class LossReasonForm(BaseForm):
    class Meta:
        model = LossReason
        fields = ["name", "order"]


class UserForm(BaseForm):
    class Meta:
        model = User
        fields = ["name", "email", "role", "is_active", "is_superuser", "task_emails"]
        labels = {"is_superuser": "Superusuario (ve informes y ajustes)"}


class RoleForm(BaseForm):
    mcp_tools = forms.MultipleChoiceField(
        label="Herramientas del asistente (MCP)", required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Sin marcar ninguna, el rol tiene todas las que sus permisos admiten. Marca solo algunas para recortarlo.")

    class Meta:
        model = Role
        fields = ["name", "view_scope", "edit_scope", "can_delete", "can_export", "can_manage_catalog", "can_manage_pipeline", "can_manage_events", "can_manage_knowledge", "can_manage_incentives", "can_use_api", "mcp_tools"]

    def __init__(self, *args, **kwargs):
        from apps.integrations.mcp import tool_choices

        super().__init__(*args, **kwargs)
        self.fields["mcp_tools"].choices = tool_choices()


class SubscriptionForm(BaseForm):
    class Meta:
        model = Subscription
        fields = ["company", "product", "quantity", "unit_price", "discount", "period", "started_on", "renews_on"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(billing_model="monthly")


class SubscriptionChangeForm(forms.Form):
    product = forms.ModelChoiceField(label="Plan", queryset=Product.objects.filter(billing_model="monthly"), required=False,
                                     empty_label="Sin plan de catálogo", help_text="Vacío si es una cuota que no sale del catálogo.")
    quantity = forms.DecimalField(label="Cantidad", min_value=0)
    unit_price = forms.DecimalField(label="Precio", min_value=0)
    note = forms.CharField(label="Nota", required=False, max_length=300)


class HourBankForm(BaseForm):
    class Meta:
        model = HourBank
        fields = ["company", "product", "name", "hours_sold", "hourly_rate", "sold_on", "closed"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(billing_model="hourly")


class HourEntryForm(BaseForm):
    class Meta:
        model = HourEntry
        fields = ["worked_on", "hours", "description"]


class QuoteTemplateForm(BaseForm):
    class Meta:
        model = QuoteTemplate
        fields = ["name", "intro", "terms", "validity_days", "tax_rate", "issuer_name", "issuer_address", "footer", "is_default"]


class EmailListField(forms.CharField):
    """Una o varias direcciones separadas por comas."""

    def clean(self, value):
        from django.core.validators import validate_email

        addresses = [part.strip() for part in super().clean(value).replace(";", ",").split(",") if part.strip()]
        for address in addresses:
            validate_email(address)
        return addresses


class QuoteSendForm(forms.Form):
    to = EmailListField(label="Para", max_length=300)
    cc = EmailListField(label="Copia (CC)", max_length=300, required=False)
    subject = forms.CharField(label="Asunto", max_length=200)
    body = forms.CharField(label="Mensaje", widget=forms.Textarea(attrs={"rows": 8}))


class WebhookForm(BaseForm):
    events = forms.MultipleChoiceField(label="Eventos (ninguno = todos)", choices=Webhook.EVENTS, required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Webhook
        fields = ["name", "url", "events", "is_active"]


class MicrosoftConfigForm(forms.Form):
    """Datos de la aplicación registrada en Microsoft Entra. El secreto se guarda cifrado y no vuelve a mostrarse."""

    tenant_id = forms.CharField(label="Id. de directorio (inquilino)", max_length=80)
    client_id = forms.CharField(label="Id. de aplicación (cliente)", max_length=80)
    client_secret = forms.CharField(label="Secreto de cliente (valor)", max_length=200, required=False, widget=forms.PasswordInput(render_value=False),
                                    help_text="Déjalo vacío para conservar el que ya hay.")


class NdaForm(forms.ModelForm):
    """Datos de la otra parte del acuerdo: salen de la empresa y el contacto, y se pueden corregir antes de generar."""

    class Meta:
        model = Nda
        fields = ["language", "signed_on", "party_name", "party_address", "party_tax_id", "party_country", "signer_name", "signer_position"]
        labels = {"language": "Idioma del acuerdo", "party_name": "Razón social del cliente", "signer_name": "Firma por el cliente"}
        widgets = {"signed_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")}
        help_texts = {
            "party_name": "La denominación legal completa, con su forma jurídica (S.L., S.A., GmbH, SE…).",
            "party_tax_id": "CIF/NIF o identificación fiscal de su país.",
            "party_country": "País conforme a cuyas leyes está constituida.",
            "signer_name": "Si lo dejas vacío, el nombre se rellena a mano al firmar.",
        }


class TeamsMeetingForm(forms.Form):
    account = forms.ModelChoiceField(label="Convoca desde", queryset=None, empty_label=None)
    subject = forms.CharField(label="Asunto", max_length=200)
    start = forms.DateTimeField(label="Cuándo", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
                                input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"])
    minutes = forms.TypedChoiceField(label="Duración", coerce=int, choices=[(30, "30 minutos"), (45, "45 minutos"), (60, "1 hora"), (90, "1 hora y media"), (120, "2 horas")])
    attendees = EmailListField(label="Asistentes", max_length=500, help_text="Correos separados por comas. Microsoft les envía la convocatoria.")
    message = forms.CharField(label="Mensaje de la convocatoria", required=False, widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, accounts, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = accounts

    def clean_start(self):
        start = self.cleaned_data["start"]
        if start < timezone.now():
            raise forms.ValidationError("La reunión no puede ser en el pasado.")
        return start
