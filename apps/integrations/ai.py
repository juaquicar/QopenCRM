"""Asistente de IA opcional: rellenar fichas desde texto pegado y buscar en lenguaje natural.

Proveedor seleccionable con AI_PROVIDER (openai o anthropic). Sin AI_API_KEY la función no aparece.
Solo se envía al proveedor el texto que el usuario pega o escribe, nunca la base de datos.
"""
import json
import urllib.error
import urllib.request

from django.conf import settings

DEFAULT_MODELS = {"openai": "gpt-4o-mini", "anthropic": "claude-haiku-5-5"}


class AIError(Exception):
    pass


def enabled():
    return bool(settings.AI_API_KEY) and settings.AI_PROVIDER in DEFAULT_MODELS


def _post(url, headers, payload):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise AIError(f"El proveedor de IA ha respondido {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIError("No se ha podido contactar con el proveedor de IA.") from exc


def complete_json(system, user):
    """Pide al modelo un objeto JSON y lo devuelve como diccionario."""
    if not enabled():
        raise AIError("El asistente de IA no está configurado.")
    provider = settings.AI_PROVIDER
    model = settings.AI_MODEL or DEFAULT_MODELS[provider]
    system += "\nResponde solo con un objeto JSON válido, sin texto adicional."
    if provider == "openai":
        data = _post("https://api.openai.com/v1/chat/completions", {"Authorization": f"Bearer {settings.AI_API_KEY}"}, {
            "model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        text = data["choices"][0]["message"]["content"]
    else:
        data = _post("https://api.anthropic.com/v1/messages", {"x-api-key": settings.AI_API_KEY, "anthropic-version": "2023-06-01"}, {
            "model": model, "max_tokens": 1024, "system": system, "messages": [{"role": "user", "content": user}],
        })
        text = "".join(block.get("text", "") for block in data.get("content", []))
    start, end = text.find("{"), text.rfind("}")
    try:
        result = json.loads(text[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise AIError("La IA no ha devuelto una respuesta utilizable.") from exc
    if not isinstance(result, dict):
        raise AIError("La IA no ha devuelto una respuesta utilizable.")
    return result


EXTRACT_FIELDS = {
    "company": {"name": "razón social o nombre comercial", "tax_id": "CIF/NIF", "website": "web", "email": "correo general", "phone": "teléfono",
                "sector": "sector de actividad", "address": "dirección postal", "city": "población", "province": "provincia", "country": "país"},
    "contact": {"first_name": "nombre de pila", "last_name": "apellidos", "position": "cargo", "email": "correo", "phone": "teléfono fijo", "mobile": "móvil"},
}


def extract(kind, text):
    """Datos de una ficha a partir de texto libre (firma de correo, tarjeta, párrafo de una web)."""
    fields = EXTRACT_FIELDS[kind]
    system = ("Extraes datos de contacto de un texto para un CRM español. Devuelve un objeto JSON con estas claves: "
              + "; ".join(f"{key} ({label})" for key, label in fields.items())
              + ". Usa solo lo que aparece en el texto: si un dato no está, omite la clave. No inventes nada.")
    data = complete_json(system, text[:6000])
    return {key: str(value).strip()[:200] for key, value in data.items() if key in fields and value not in (None, "", [])}


def search_filters(question, options, today):
    """Traduce una pregunta («lo de Plan Pro sin próxima acción de Ana») a filtros del pipeline."""
    system = (
        f"Conviertes una petición en filtros de un pipeline comercial. Hoy es {today:%Y-%m-%d}. Devuelve un objeto JSON solo con las claves que apliquen:\n"
        "q (solo el nombre propio de una cuenta u oportunidad citada, una o dos palabras; nunca la frase de la petición ni nada ya cubierto por otro filtro), status (open, won, lost, all), stage (id), assignee (id de persona), "
        "product (id), family (id), priority (1 baja, 2 normal, 3 alta), action (none = sin próxima acción, overdue = vencida, today, week), "
        "close_from y close_to (fecha AAAA-MM-DD de cierre estimado), created_from y created_to (fecha de alta).\n"
        f"Valores disponibles: {json.dumps(options, ensure_ascii=False)}\n"
        "Usa únicamente ids de esa lista. Si no hay ningún nombre propio de cuenta u oportunidad, omite q."
    )
    data = complete_json(system, question[:500])
    # el texto libre filtra por coincidencia literal: una frase entera dejaría el resultado vacío
    text = str(data.get("q", "")).strip().lower()
    if len(text.split()) > 3:
        data.pop("q")
    elif text:
        # si el texto libre es en realidad una persona, producto, etapa o familia, va a su filtro y no a q
        for group, key in (("personas", "assignee"), ("productos", "product"), ("etapas", "stage"), ("familias", "family")):
            match = next((item for item in options.get(group, []) if text in str(item["name"]).lower() or str(item["name"]).lower() in text), None)
            if match:
                data.pop("q", None)
                data.setdefault(key, match["id"])
                break
    allowed = {"q", "status", "stage", "assignee", "product", "family", "priority", "action", "close_from", "close_to", "created_from", "created_to"}
    return {key: str(value) for key, value in data.items() if key in allowed and value not in (None, "", [])}
