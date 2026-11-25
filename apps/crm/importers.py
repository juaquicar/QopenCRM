"""Importación CSV de cuentas y contactos con deduplicación."""
import csv
import io
import re
import unicodedata

from django.db import transaction
from django.db.models import Q

from .models import GENERIC_DOMAINS, Company, Contact, normalize_domain, normalize_name

# cabeceras admitidas (en minúsculas, sin tildes) → campo
COLUMNS = {
    "empresa": "company", "cuenta": "company", "organizacion": "company", "company": "company", "razon social": "company",
    "cif": "tax_id", "nif": "tax_id", "tax_id": "tax_id",
    "web": "website", "website": "website", "url": "website",
    "telefono empresa": "company_phone", "email empresa": "company_email",
    "sector": "sector", "direccion": "address", "poblacion": "city", "ciudad": "city", "provincia": "province", "pais": "country",
    "nombre": "first_name", "first_name": "first_name", "apellidos": "last_name", "apellido": "last_name", "last_name": "last_name",
    "email": "email", "correo": "email", "e-mail": "email",
    "telefono": "phone", "phone": "phone", "movil": "mobile", "mobile": "mobile",
    "cargo": "position", "puesto": "position", "position": "position",
    "base legal": "legal_basis", "consentimiento": "consent", "origen consentimiento": "consent_source",
    "notas": "notes",
}
LEGAL = {"consentimiento": "consent", "contrato": "contract", "interes legitimo": "legitimate"}
TRUE = {"1", "si", "sí", "s", "x", "true", "yes", "y"}


def _key(text):
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9\-]+", " ", text).split())


def _read(upload):
    raw = upload.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return csv.DictReader(io.StringIO(text), dialect=dialect)


def _find_company(row):
    cond = Q(pk__in=[])
    if row.get("tax_id"):
        cond |= Q(tax_id=row["tax_id"].replace(" ", "").replace("-", "").replace(".", "").upper())
    key = normalize_name(row.get("company", ""))
    if key:
        cond |= Q(name_key=key)
    domain = normalize_domain(row.get("website", ""))
    if domain and domain not in GENERIC_DOMAINS:
        cond |= Q(domain=domain)
    return Company.objects.filter(cond).first()


def import_csv(upload, user, dry_run=True):
    """Crea lo que falta y completa lo que existe; nunca pisa un dato ya relleno."""
    reader = _read(upload)
    mapping = {h: COLUMNS[_key(h)] for h in (reader.fieldnames or []) if _key(h) in COLUMNS}
    unknown = [h for h in (reader.fieldnames or []) if h not in mapping]
    stats = {"companies_new": 0, "companies_found": 0, "contacts_new": 0, "contacts_found": 0, "skipped": 0}
    errors, preview = [], []
    with transaction.atomic():
        for number, raw in enumerate(reader, start=2):
            row = {field: (raw.get(header) or "").strip() for header, field in mapping.items()}
            if not row.get("company") and not row.get("email") and not row.get("first_name"):
                stats["skipped"] += 1
                continue
            company = None
            if row.get("company"):
                company = _find_company(row)
                if company:
                    stats["companies_found"] += 1
                else:
                    company = Company(name=row["company"], owner=user)
                    stats["companies_new"] += 1
                changed = not company.pk
                for field, source in (("tax_id", "tax_id"), ("website", "website"), ("phone", "company_phone"), ("email", "company_email"),
                                      ("sector", "sector"), ("address", "address"), ("city", "city"), ("province", "province"), ("country", "country")):
                    if row.get(source) and not getattr(company, field):
                        setattr(company, field, row[source])
                        changed = True
                if changed:
                    company.save()
            if row.get("first_name") or row.get("email"):
                email = row.get("email", "").lower()
                if email and "@" not in email:
                    errors.append(f"Fila {number}: correo no válido «{email}»")
                    email = ""
                contact = Contact.objects.filter(email=email).first() if email else None
                if not contact and company and row.get("first_name"):
                    contact = company.contacts.filter(first_name__iexact=row["first_name"], last_name__iexact=row.get("last_name", "")).first()
                if contact:
                    stats["contacts_found"] += 1
                else:
                    contact = Contact(first_name=row.get("first_name") or email.split("@")[0], company=company, owner=user)
                    stats["contacts_new"] += 1
                for field in ("last_name", "email", "phone", "mobile", "position", "notes", "consent_source"):
                    value = email if field == "email" else row.get(field, "")
                    if value and not getattr(contact, field):
                        setattr(contact, field, value)
                if not contact.company_id and company:
                    contact.company = company
                basis = LEGAL.get(_key(row.get("legal_basis", "")))
                if basis and contact.legal_basis == Contact.LegalBasis.UNKNOWN:
                    contact.legal_basis = basis
                if row.get("consent", "").lower() in TRUE:
                    contact.consent_marketing = True
                contact.save()
            if len(preview) < 8:
                preview.append({"company": row.get("company", ""), "contact": f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(), "email": row.get("email", "")})
        if dry_run:
            transaction.set_rollback(True)
    return {"dry_run": dry_run, "stats": stats, "errors": errors[:30], "unknown": unknown, "mapped": mapping, "preview": preview}
