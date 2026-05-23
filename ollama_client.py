import json
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen3.5:0.8b"

# Nota: usamos salida JSON estricta para que no devuelva texto libre.
# Si el modelo falla o devuelve algo no parseable, hacemos fallback a reglas simples.

def _fallback(descripcion: str) -> dict:
    d = (descripcion or "").lower()
    # Heurísticas mínimas
    if any(k in d for k in ["sin internet", "no hay internet", "caído", "caido", "sin servicio", "toda la", "toda la empresa", "toda la sede", "producción", "produccion"]):
        return {"prioridad": "Alta", "tipo": "Incidente"}
    if any(k in d for k in ["no puedo enviar", "no se pueden enviar", "error", "falla", "lento", "intermitente"]):
        return {"prioridad": "Media", "tipo": "Incidente"}
    return {"prioridad": "Baja", "tipo": "Requerimiento"}


def analizar_ticket(descripcion: str) -> dict:
    """Clasifica prioridad (Alta/Media/Baja) y tipo (Incidente/Requerimiento) con IA local.

    Retorna dict:
      {"prioridad": "Alta|Media|Baja", "tipo": "Incidente|Requerimiento"}
    """

    # Prompt muy restrictivo (evita que responda siempre Media)
    prompt = f"""
Eres un clasificador de tickets de soporte IT.

Tarea: devuelve SOLO JSON con dos campos: prioridad y tipo.

Definiciones:
- Prioridad Alta: caída total del servicio, sin conectividad, operación detenida, afecta a muchos usuarios/sedes, incidente de seguridad.
- Prioridad Media: falla importante pero parcial, degradación notable, afecta a un área o varios usuarios pero no detiene toda la operación.
- Prioridad Baja: solicitud de servicio, consulta, cambio planificado, mejora, acceso/permiso sin urgencia.

Tipo:
- Incidente: algo que funcionaba y dejó de funcionar o está fallando.
- Requerimiento: solicitud de acceso/cambio/instalación/consulta.

Entrada (descripción del usuario):
""" + descripcion + """

Responde SOLO:
{"prioridad":"Alta|Media|Baja","tipo":"Incidente|Requerimiento"}
""".strip()

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        # Pedimos formato json (Ollama lo soporta) para que sea parseable.
        "format": "json",
        "options": {
            "temperature": 0,
            "top_p": 0.9,
            "num_predict": 80
        },
        "keep_alive": "0"
    }

    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=60)
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        data = json.loads(raw)
        pr = (data.get("prioridad") or "").capitalize()
        tp = (data.get("tipo") or "").capitalize()
        if pr not in {"Alta", "Media", "Baja"}:
            raise ValueError("Prioridad fuera de rango")
        if tp not in {"Incidente", "Requerimiento"}:
            raise ValueError("Tipo fuera de rango")
        return {"prioridad": pr, "tipo": tp}
    except Exception:
        return _fallback(descripcion)
