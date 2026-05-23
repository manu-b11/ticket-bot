
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
 
import json
import re
import requests
from datetime import datetime
 
from ollama_client import analizar_ticket
from rag_engine import cargar_kb, buscar_respuesta
 
# =========================================================
# SupportSync - Backend MVP
# - Wizard 8 campos + caso previo opcional
# - Prioridad al final (sugerida/validada por IA local)
# - Ecuación (score) para recomendar/escalar a humano (NV1)
# - RAG para FAQ (base delimitada)
# =========================================================
 
app = FastAPI()
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# -------------------- KB (RAG) --------------------
try:
    cargar_kb()
except Exception as e:
    print("⚠️ No se pudo cargar la KB (RAG):", e)
 
 
# -------------------- Modelos API --------------------
class ChatMessage(BaseModel):
    user_id: str
    message: str
 
 
# -------------------- Estado (MVP en memoria) --------------------
sessions = {}        # user_id -> { step:int, ticket:dict, flags... }
tickets = {}         # numero_caso -> ticket final
notifications = {}   # user_id -> [ {ts,text}, ... ]
 
COUNTERS = {
    "id": 0,
    "year": datetime.now().year,
    "seq": 0,
}
 
TIPOS_CATEGORIA = {"software", "hardware", "red", "acceso", "otro"}
PRIORIDADES = {"alta", "media", "baja"}
 
KW_IMPACTO_ALTO = {"toda", "todos", "nadie", "empresa", "sede", "global", "general", "producción", "produccion"}
KW_CAIDA = {"caído", "caido", "sin servicio", "no hay servicio", "no funciona", "caida", "caída", "down"}
KW_SEGURIDAD = {"ransom", "ransomware", "phishing", "hack", "brecha", "virus", "malware", "intrusión", "intrusion"}
 
 
# -------------------- Helpers --------------------
def notify(user_id: str, text: str):
    notifications.setdefault(user_id, []).append({
        "ts": datetime.utcnow().isoformat(),
        "text": text,
    })
 
 
def next_id() -> int:
    COUNTERS["id"] += 1
    return COUNTERS["id"]
 
 
def next_numero_caso() -> str:
    y = datetime.now().year
    if COUNTERS["year"] != y:
        COUNTERS["year"] = y
        COUNTERS["seq"] = 0
    COUNTERS["seq"] += 1
    return f"NET-{y}-{COUNTERS['seq']:03d}"
 
 
def valid_nonempty(s: str, min_len: int = 2) -> bool:
    return bool(s.strip()) and len(s.strip()) >= min_len
 
 
def valid_fullname(s: str) -> bool:
    return len(s.strip().split()) >= 2
 
 
def valid_phone(s: str) -> bool:
    return s.isdigit() and 7 <= len(s) <= 15
 
 
def build_ticket_summary(ticket: dict) -> str:
    t = ticket or {}
    return (
        f"📋 RESUMEN DEL TICKET\n"
        f"🧾 Número de caso: {t.get('numero_caso', '(sin asignar)')}\n"
        f"📌 Tipo: {t.get('tipo', '')}\n"
        f"📝 Título: {t.get('titulo', '')}\n"
        f"🏢 Cliente: {t.get('cliente', '')}\n"
        f"🧩 Descripción: {t.get('descripcion', '')}\n"
        f"👤 Nombre: {t.get('nombre', '')}\n"
        f"💼 Cargo: {t.get('cargo', '')}\n"
        f"📞 Número de contacto: {t.get('numero_contacto', '')}\n"
        f"🔁 Caso previo: {t.get('caso_previo', None)}\n"
        f"⚡ Prioridad: {t.get('prioridad', '')}\n"
        f"📍 Estado: {t.get('estado', 'Abierto')}"
    )
 
 
# -------------------- IA local (Ollama) --------------------
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:0.8b"
 
 
def _limpiar_json(raw: str) -> str:
    """Elimina bloques <think>...</think> que algunos modelos insertan antes del JSON."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else "{}"
 
 
def ia_validar_prioridad(descripcion: str, tipo: str, prioridad_usuario: str) -> dict:
    """Valida/corrige prioridad propuesta usando IA local."""
    prompt = (
        "Eres un analista de mesa de ayuda. Valida la PRIORIDAD del ticket.\n\n"
        "Reglas:\n"
        "- Alta: caída total del servicio, operación detenida, incidente crítico, seguridad.\n"
        "- Media: afecta parcialmente o degradación importante.\n"
        "- Baja: solicitud, consulta, cambio no urgente.\n\n"
        f"Datos:\n- Tipo: {tipo}\n- Descripción: {descripcion}\n"
        f"- Prioridad propuesta: {prioridad_usuario}\n\n"
        'Devuelve SOLO este JSON (sin texto adicional):\n'
        '{"valida": true, "prioridad_final": "Alta|Media|Baja", "razon": "..."}'
    )
 
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 170, "num_ctx": 2048},
        "keep_alive": "0",
    }
 
    try:
        r = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=60)
        r.raise_for_status()
        raw = r.json().get("response", "{}")
        return json.loads(_limpiar_json(raw))
    except Exception:
        return {
            "valida": True,
            "prioridad_final": prioridad_usuario,
            "razon": "No se pudo validar con IA, se mantiene la prioridad indicada.",
        }
 
 
def ia_sugerir_prioridad(descripcion: str, tipo: str) -> str:
    """Sugiere prioridad con analizar_ticket()."""
    try:
        res = analizar_ticket(f"Tipo: {tipo}. Descripción: {descripcion}")
        pr = (res.get("prioridad") or "Media").strip().capitalize()
        if pr not in {"Alta", "Media", "Baja"}:
            pr = "Media"
        return pr
    except Exception:
        return "Media"
 
 
# -------------------- Ecuación de escalamiento --------------------
def calcular_score_escalamiento(ticket: dict, rag_sin_fuente: bool = False, usuario_pidio_agente: bool = False) -> dict:
    desc = (ticket.get("descripcion") or "").lower()
    prioridad = (ticket.get("prioridad") or ticket.get("prioridad_sugerida") or "Media").capitalize()
 
    P = 100 if prioridad == "Alta" else 60 if prioridad == "Media" else 20
 
    I = 20
    if any(k in desc for k in KW_IMPACTO_ALTO):
        I = 80
    if any(k in desc for k in KW_CAIDA):
        I = max(I, 90)
 
    S = 100 if any(k in desc for k in KW_SEGURIDAD) else 0
 
    genericos = {"no funciona", "no sirve", "ayuda", "urgente"}
    A = 0
    if len(desc) < 20:
        A = 80
    if any(g in desc for g in genericos) and len(desc) < 35:
        A = max(A, 70)
 
    R = 30 if ticket.get("caso_previo") else 0
 
    K = 0
    if rag_sin_fuente:
        K = 70
    if usuario_pidio_agente:
        K = 100
 
    score = round(0.25 * P + 0.30 * I + 0.20 * S + 0.10 * A + 0.05 * R + 0.10 * K)
 
    if score >= 70:
        decision = "ESCALAR_YA"
    elif score >= 50:
        decision = "RECOMENDAR_ESCALAR"
    else:
        decision = "NO_ESCALAR"
 
    razones = []
    if prioridad == "Alta":
        razones.append("prioridad alta")
    if I >= 80:
        razones.append("impacto alto o caída")
    if S == 100:
        razones.append("posible incidente de seguridad")
    if A >= 70:
        razones.append("descripción ambigua")
    if R > 0:
        razones.append("hay caso previo")
    if rag_sin_fuente:
        razones.append("sin respaldo documental (RAG)")
    if usuario_pidio_agente:
        razones.append("usuario pidió agente")
 
    return {"score": score, "decision": decision, "razones": razones}
 
 
# -------------------- Bot (wizard) --------------------
def process_message(message: str, user_id: str) -> str:
    msg = (message or "").strip()
 
    # FAQ/RAG
    if msg.lower().startswith("faq:") or "?" in msg:
        pregunta = msg[4:].strip() if msg.lower().startswith("faq:") else msg
        if not pregunta:
            return "⚠️ Escribe una pregunta válida."
        respuesta = buscar_respuesta(pregunta)
        if user_id not in sessions:
            sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": False}
        sessions[user_id]["rag_sin_fuente"] = ("no tengo" in respuesta.lower() and "base" in respuesta.lower())
        return respuesta + "\n\nSi quieres, escribe: 'hablar con agente'."
 
    # Solicitud explícita de agente
    if msg.lower() in {"hablar con agente", "agente", "nv1", "escalar"}:
        if user_id not in sessions:
            sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": True}
        else:
            sessions[user_id]["usuario_pidio_agente"] = True
        t = sessions[user_id].get("ticket", {})
        return "✅ Te transfiero a NV1 con el contexto.\n\n" + build_ticket_summary(t)
 
    # Inicializar sesión
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": False}
 
    state = sessions[user_id]
    step = state["step"]
    ticket = state["ticket"]
 
    if step == 0:
        state["step"] = 1
        return "Hola 👋 Describe el problema (mínimo 10 caracteres)."
 
    if step == 1:
        if len(msg) < 10:
            return "⚠️ Describe un poco más el problema (mínimo 10 caracteres)."
        ticket["descripcion"] = msg
        state["step"] = 2
        return "¿Cuál es el tipo? (Software/Hardware/Red/Acceso/Otro)"
 
    if step == 2:
        t = msg.lower()
        if t not in TIPOS_CATEGORIA:
            return "⚠️ Tipo inválido. Usa: Software, Hardware, Red, Acceso u Otro."
        ticket["tipo"] = t.capitalize()
        state["step"] = 3
        return "Título del caso (5–80 caracteres):"
 
    if step == 3:
        if not (5 <= len(msg) <= 80):
            return "⚠️ El título debe tener entre 5 y 80 caracteres."
        ticket["titulo"] = msg
        state["step"] = 4
        return "Cliente (empresa):"
 
    if step == 4:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cliente inválido."
        ticket["cliente"] = msg
        state["step"] = 5
        return "Nombre completo (mínimo 2 palabras):"
 
    if step == 5:
        if not valid_fullname(msg):
            return "⚠️ Escribe nombre y apellido (mínimo 2 palabras)."
        ticket["nombre"] = msg
        ticket["contacto"] = msg
        state["step"] = 6
        return "Cargo:"
 
    if step == 6:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cargo inválido."
        ticket["cargo"] = msg
        state["step"] = 7
        return "Número de contacto (solo números, 7–15 dígitos):"
 
    if step == 7:
        if not valid_phone(msg):
            return "⚠️ Número inválido. Solo dígitos (7–15)."
        ticket["numero_contacto"] = msg
        state["step"] = 8
        return "¿Tienes un número de caso previo? (sí/no)"
 
    if step == 8:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            state["step"] = 9
            return "Ingresa el número del caso previo:"
        ticket["caso_previo"] = None
        state["step"] = 10
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"✅ Antes del resumen: Prioridad sugerida por IA = {pr}. ¿Confirmas? (sí/no)"
 
    if step == 9:
        if not valid_nonempty(msg, 3):
            return "⚠️ Número de caso previo inválido."
        ticket["caso_previo"] = msg
        state["step"] = 10
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"✅ Antes del resumen: Prioridad sugerida por IA = {pr}. ¿Confirmas? (sí/no)"
 
    if step == 10:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            ticket["prioridad"] = ticket.get("prioridad_sugerida", "Media")
            state["step"] = 12
        else:
            state["step"] = 11
            return "Indica prioridad (Alta/Media/Baja). La IA la validará:"
 
    if step == 11:
        pr = msg.strip().capitalize()
        if pr.lower() not in PRIORIDADES:
            return "⚠️ Prioridad inválida. Usa: Alta, Media o Baja."
        verdict = ia_validar_prioridad(
            descripcion=ticket["descripcion"],
            tipo=ticket["tipo"],
            prioridad_usuario=pr,
        )
        ticket["prioridad_validacion"] = verdict
        ticket["prioridad"] = verdict.get("prioridad_final", pr)
        state["step"] = 12
 
    if step == 12:
        if "numero_caso" not in ticket:
            ticket["numero_caso"] = next_numero_caso()
        ticket.setdefault("estado", "Abierto")
 
        esc = calcular_score_escalamiento(
            ticket,
            rag_sin_fuente=bool(state.get("rag_sin_fuente", False)),
            usuario_pidio_agente=bool(state.get("usuario_pidio_agente", False)),
        )
        ticket["escalamiento"] = esc
 
        razones_str = ", ".join(esc["razones"]) if esc["razones"] else "sin señales adicionales"
 
        if esc["decision"] == "ESCALAR_YA":
            state["step"] = 999
            return (
                f"🚨 Este caso parece crítico.\n"
                f"📈 Score: {esc['score']}/100\n"
                f"Motivos: {razones_str}\n\n"
                "¿Quieres que te conecte con un agente humano (NV1) ahora? (sí/no)"
            )
 
        state["step"] = 13
        recomendacion = (
            "⚠️ Recomendación: Considera escalar a NV1."
            if esc["decision"] == "RECOMENDAR_ESCALAR"
            else "✅ Recomendación: Puede gestionarse sin agente por ahora."
        )
        return (
            build_ticket_summary(ticket)
            + f"\n\n📈 Score de escalamiento: {esc['score']}/100\n"
            + f"{recomendacion}\n"
            + f"Motivos: {razones_str}\n\n"
            + "¿Confirmas crear el ticket? (sí/no)"
        )
 
    if step == 999:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            notify(user_id, f"🔔 Caso {ticket.get('numero_caso', '(sin asignar)')} escalado a NV1 con contexto")
            state["step"] = 13
            return "✅ Conectando con soporte NV1...\n\n📦 Contexto:\n" + build_ticket_summary(ticket)
        state["step"] = 13
        return build_ticket_summary(ticket) + "\n\n¿Confirmas crear el ticket? (sí/no)"
 
    if step == 13:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low == "no":
            sessions.pop(user_id, None)
            return "❌ Ticket cancelado."
 
        tid = next_id()
        numero_caso = ticket.get("numero_caso") or next_numero_caso()
 
        final_ticket = {
            "tipo": ticket.get("tipo", "Software"),
            "numero_caso": numero_caso,
            "titulo": ticket.get("titulo", ""),
            "descripcion": ticket.get("descripcion", ""),
            "nombre": ticket.get("nombre", ""),
            "estado": ticket.get("estado", "Abierto"),
            "cliente": ticket.get("cliente", ""),
            "id": tid,
            "prioridad": ticket.get("prioridad", "Media"),
            "contacto": ticket.get("contacto", ticket.get("nombre", "")),
            "cargo": ticket.get("cargo", ""),
            "numero_contacto": ticket.get("numero_contacto"),
            "caso_previo": ticket.get("caso_previo"),
            "escalamiento": ticket.get("escalamiento"),
        }
 
        tickets[numero_caso] = final_ticket
        notify(user_id, f"✅ Caso {numero_caso} recibido. Estado: {final_ticket['estado']}")
        sessions.pop(user_id, None)
 
        esc = final_ticket.get("escalamiento", {})
        if esc.get("decision") == "ESCALAR_YA":
            return (
                f"✅ Ticket creado. Número de caso: {numero_caso}\n"
                "🚨 Este caso debería escalarse a NV1. Escribe 'hablar con agente' para transferir."
            )
 
        return f"✅ Ticket creado. Número de caso: {numero_caso}"
 
    return "No entendí. Escribe tu problema o una pregunta (FAQ)."
 
 
# -------------------- Endpoints --------------------
@app.post("/chat")
def chat(data: ChatMessage):
    response = process_message(data.message, data.user_id)
    return {"response": response}
 
 
@app.get("/notifications/{user_id}")
def get_notifications(user_id: str):
    msgs = notifications.get(user_id, [])
    notifications[user_id] = []
    return {"notifications": msgs}
 
 
@app.get("/ticket/{numero_caso}")
def get_ticket(numero_caso: str):
    if numero_caso not in tickets:
        return {"ok": False, "error": "numero_caso no existe"}
    return {"ok": True, "ticket": tickets[numero_caso]}
 
 
@app.post("/ticket/{numero_caso}/status")
def update_status(numero_caso: str, payload: dict = Body(...)):
    if numero_caso not in tickets:
        return {"ok": False, "error": "numero_caso no existe"}
 
    estado = (payload.get("estado") or "").strip()
    user_id = (payload.get("user_id") or "").strip()
 
    if not estado:
        return {"ok": False, "error": "Falta estado"}
    if not user_id:
        return {"ok": False, "error": "Falta user_id"}
 
    tickets[numero_caso]["estado"] = estado
    notify(user_id, f"🔔 Caso {numero_caso} actualizado. Estado: {estado}")
 
    return {"ok": True, "numero_caso": numero_caso, "estado": estado}
 
 
@app.post("/handoff")
def handoff(payload: dict = Body(...)):
    user_id = (payload.get("user_id") or "").strip()
    current_ticket = sessions.get(user_id, {}).get("ticket", {})
    return {"ok": True, "context": current_ticket}
 
 
@app.get("/")
def root():
    return {"status": "ok"}
 