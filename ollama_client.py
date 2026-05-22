import requests
import json

OLLAMA_URL = "http://localhost:11434/api/generate"

MODEL = "qwen3.5:0.8b"

def analizar_ticket(descripcion):

    prompt = f"""
    Analiza el siguiente problema de soporte:

    "{descripcion}"

    Devuelve SOLO este JSON:
    {{
        "prioridad": "Alta o Media o Baja",
        "tipo": "Incidente o Requerimiento"
    }}

    Reglas:
    - Alta: sistema caído, sin servicio
    - Media: problema importante pero no crítico
    - Baja: solicitud o consulta
    """

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0,
            "num_predict": 100
        }
    }

    response = requests.post(OLLAMA_URL, json=payload)
    result = response.json()["response"]

    try:
        return json.loads(result)
    except:
        return {
            "prioridad": "Media",
            "tipo": "Incidente"
        }