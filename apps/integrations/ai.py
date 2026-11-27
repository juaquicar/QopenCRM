"""Asistente de IA opcional: rellenar fichas desde texto pegado o buscando en internet, y buscar en lenguaje natural.

Proveedor seleccionable con AI_PROVIDER (openai o anthropic). Sin AI_API_KEY la función no aparece.
Solo se envía al proveedor el texto que el usuario pega o escribe (y, al buscar en internet, el nombre que hay
en el formulario), nunca la base de datos.
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


WEB_TIMEOUT = 50  # una búsqueda en internet tarda más; por debajo del tope de 60 s del servidor web


def _post(url, headers, payload, timeout=30):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise AIError(f"El proveedor de IA ha respondido {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AIError("No se ha podido contactar con el proveedor de IA.") from exc


def _parse_object(text):
    start, end = text.find("{"), text.rfind("}")
    try:
        result = json.loads(text[start:end + 1])
    except (ValueError, TypeError) as exc:
        raise AIError("La IA no ha devuelto una respuesta utilizable.") from exc
    if not isinstance(result, dict):
        raise AIError("La IA no ha devuelto una respuesta utilizable.")
    return result


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
    return _parse_object(text)


def search_json(system, user):
    """Como complete_json, pero el modelo busca antes en internet con la herramienta de búsqueda del proveedor."""
    if not enabled():
        raise AIError("El asistente de IA no está configurado.")
    provider = settings.AI_PROVIDER
    model = settings.AI_MODEL or DEFAULT_MODELS[provider]
    system += "\nCuando termines de buscar, responde solo con un objeto JSON válido, sin texto adicional."
    if provider == "openai":
        data = _post("https://api.openai.com/v1/responses", {"Authorization": f"Bearer {settings.AI_API_KEY}"}, {
            "model": model, "tools": [{"type": "web_search"}], "instructions": system, "input": user,
        }, timeout=WEB_TIMEOUT)
        text = "".join(part.get("text", "") for item in data.get("output", []) if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text")
    else:
        headers = {"x-api-key": settings.AI_API_KEY, "anthropic-version": "2023-06-01"}
        messages = [{"role": "user", "content": user}]
        for _ in range(3):  # la búsqueda corre en el proveedor; si pausa el turno, se le devuelve tal cual para que siga
            data = _post("https://api.anthropic.com/v1/messages", headers, {
                "model": model, "max_tokens": 4096, "system": system, "messages": messages,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
            }, timeout=WEB_TIMEOUT)
            if data.get("stop_reason") != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": data.get("content", [])}]
        if data.get("stop_reason") == "refusal":
            raise AIError("La IA ha rechazado la búsqueda.")
        # el JSON va en el último bloque de texto; los anteriores son comentarios entre búsquedas
        texts = [block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"]
        text = next((t for t in reversed(texts) if "{" in t), "")
    return _parse_object(text)


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


LOOKUP_CLUES = {
    "company": {"name": "nombre", "website": "web", "tax_id": "CIF/NIF", "city": "población", "province": "provincia", "country": "país"},
    "contact": {"first_name": "nombre", "last_name": "apellidos", "company": "empresa", "position": "cargo", "email": "correo"},
}
LOOKUP_SUBJECT = {
    "company": ("una empresa", "Fuentes preferidas: su web oficial (aviso legal, contacto), registros mercantiles y directorios de empresas. "
                               "El CIF/NIF y la dirección deben ser los de la razón social, tal como figuran en su aviso legal o en el registro."),
    "contact": ("un profesional", "Busca solo datos profesionales publicados por la propia persona o por su empresa (web corporativa, LinkedIn, notas de prensa). "
                                  "Nunca deduzcas un correo o un teléfono a partir de un patrón: inclúyelos solo si aparecen publicados tal cual. "
                                  "No incluyas datos personales ajenos a su actividad profesional."),
}


def lookup(kind, clues, notes=""):
    """Datos públicos de una empresa o de un profesional buscados en internet a partir de lo ya escrito en el formulario."""
    fields = EXTRACT_FIELDS[kind]
    subject, guidance = LOOKUP_SUBJECT[kind]
    system = (f"Buscas en internet los datos públicos de {subject} para completar su ficha en un CRM español. Devuelve un objeto JSON con estas claves: "
              + "; ".join(f"{key} ({label})" for key, label in fields.items())
              + "; source (URL de la página de la que sale la mayor parte de los datos). " + guidance
              + " Si hay varios candidatos y no puedes saber cuál es, no elijas: devuelve un objeto vacío. "
              "Omite la clave de cualquier dato que no hayas encontrado. No inventes nada.")
    known = "\n".join(f"{LOOKUP_CLUES[kind][key]}: {value}" for key, value in clues.items() if key in LOOKUP_CLUES[kind] and value)
    user = f"Lo que ya se sabe:\n{known}" + (f"\n\nOtras pistas:\n{notes[:2000]}" if notes else "")
    data = search_json(system, user)
    source = str(data.get("source") or "").strip()
    return {
        "fields": {key: str(value).strip()[:200] for key, value in data.items() if key in fields and value not in (None, "", [])},
        "source": source[:300] if source.startswith(("http://", "https://")) else "",
    }


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
