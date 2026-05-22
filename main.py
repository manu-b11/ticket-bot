from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from database import engine, get_db
from models import Ticket, Base
from schemas import TicketCreate, TicketResponse
from sqlalchemy.orm import Session
from fastapi import Depends

from pydantic import BaseModel


import requests
from datetime import datetime

from ollama_client import analizar_ticket
from rag_engine import cargar_kb, buscar_respuesta


# ---------------- APP ----------------
app = FastAPI()
Base.metadata.create_all(bind=engine)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # en producción: dominio
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cargar KB (RAG) una sola vez al iniciar
try:
    cargar_kb()
except Exception as e:
    print("⚠️ No se pudo cargar la KB (RAG). Motivo:", e)


# ---------------- MODELOS ----------------
class ChatMessage(BaseModel):
    user_id: str
    message: str


# ---------------- STORAGE (MVP en memoria) ----------------
sessions = {}        # user_id -> {"step": int, "ticket": dict}        
notifications = {}   # user_id -> [ {ts,text}, ... ]



# ---------------- HELPERS ----------------
TIPOS_CATEGORIA = {"software", "hardware", "red", "acceso", "otro"}
PRIORIDADES = {"alta", "media", "baja"}

def notify(user_id: str, text: str):
    notifications.setdefault(user_id, []).append({
        "ts": datetime.utcnow().isoformat(),
        "text": text
    })

def valid_nonempty(s: str, min_len: int = 2) -> bool:
    return bool(s.strip()) and len(s.strip()) >= min_len

def valid_fullname(s: str) -> bool:
    return len(s.strip().split()) >= 2

def valid_phone(s: str) -> bool:
    return s.isdigit() and 7 <= len(s) <= 15


OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:0.8b"

def ia_validar_prioridad(descripcion: str, tipo: str, prioridad_usuario: str) -> dict:
    """
    Devuelve dict:
    {
      "valida": true/false,
      "prioridad_final": "Alta/Media/Baja",
      "razon": "..."
    }
    """
    schema = {
        "type": "object",
        "properties": {
            "valida": {"type": "boolean"},
            "prioridad_final": {"type": "string", "enum": ["Alta", "Media", "Baja"]},
            "razon": {"type": "string"}
        },
        "required": ["valida", "prioridad_final", "razon"]
    }

    prompt = f"""
Eres un analista de mesa de ayuda. Debes validar la PRIORIDAD elegida para un ticket.
Reglas:
- Alta: caída total del servicio, operación detenida, incidente crítico.
- Media: afecta parcialmente o degradación importante.
- Baja: solicitud, consulta, cambio no urgente.

Datos:
- Tipo: {tipo}
- Descripción: {descripcion}
- Prioridad propuesta por el usuario: {prioridad_usuario}

Tarea:
1) Decide si la prioridad propuesta es válida.
2) Si NO es válida, propone la prioridad correcta.
Devuelve SOLO JSON válido.
""".strip()

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": schema,
        "options": {"temperature": 0, "num_predict": 160, "num_ctx": 2048},
        "keep_alive": "0"
    }

    r = requests.post(OLLAMA_GENERATE_URL, json=payload, timeout=60)
    r.raise_for_status()
    raw = r.json()["response"]

    import json
    return json.loads(raw)


def ia_sugerir_prioridad(descripcion: str, tipo: str) -> dict:
    """
    Usa tu función existente analizar_ticket() para sugerir prioridad (rápido).
    Devuelve { "prioridad": "Alta/Media/Baja", "tipo": "..." }.
    """
    try:
        res = analizar_ticket(f"Tipo: {tipo}. Descripción: {descripcion}") or {}
    except Exception as e:
        print("⚠️ Error analizar_ticket:", e)
        res = {}
    # Normaliza
    prioridad = res.get("prioridad", "Media")
    if prioridad.lower() in PRIORIDADES:
        prioridad = prioridad.capitalize()
    if prioridad not in {"Alta", "Media", "Baja"}:
        prioridad = "Media"
    return {"prioridad": prioridad}


# ---------------- BOT LOGIC ----------------
def process_message(message: str, user_id: str, db: Session) -> str:
    msg = (message or "").strip()

    # FAQ con RAG (base delimitada)
    if msg.lower().startswith("faq:"):
        pregunta = msg[4:].strip()
        if not pregunta:
            return "⚠️ Escribe una pregunta después de 'faq:'."
        return buscar_respuesta(pregunta)

    if "?" in msg:
        respuesta = buscar_respuesta(msg)
        return respuesta + "\n\nSi quieres, escribe: 'hablar con agente' para escalar a NV1."

    # Handoff a NV1
    if msg.lower() in {"hablar con agente", "agente", "nv1", "escalar"}:
        current_ticket = sessions.get(user_id, {}).get("ticket", {})
        resumen = build_ticket_summary(current_ticket, include_priority=True)
        notify(user_id, "🔔 Transferencia a NV1 solicitada. Contexto enviado.")
        return "✅ Te transfiero a NV1 sin repetir información.\n\n📦 Contexto:\n" + resumen

    # Inicializar sesión
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}}

    state = sessions[user_id]
    step = state["step"]
    ticket = state["ticket"]

    # ---- STEP 0: inicio
    if step == 0:
        state["step"] = 1
        return (
            "Hola 👋 Vamos a crear tu ticket.\n\n"
            "1) Describe el problema (mínimo 10 caracteres)."
        )

    # ---- STEP 1: descripción
    if step == 1:
        if len(msg) < 10:
            return "⚠️ Describe un poco más el problema (mínimo 10 caracteres)."
        ticket["descripcion"] = msg
        state["step"] = 2
        return "2) ¿Cuál es el tipo? (Software / Hardware / Red / Acceso / Otro)"

    # ---- STEP 2: tipo (categoría)
    if step == 2:
        t = msg.lower()
        if t not in TIPOS_CATEGORIA:
            return "⚠️ Tipo inválido. Usa: Software, Hardware, Red, Acceso u Otro."
        ticket["tipo"] = t.capitalize()  # "Software", etc.
        state["step"] = 3
        return "3) Escribe el título del caso (5–80 caracteres)."

    # ---- STEP 3: título
    if step == 3:
        if not (5 <= len(msg) <= 80):
            return "⚠️ El título debe tener entre 5 y 80 caracteres."
        ticket["titulo"] = msg
        state["step"] = 4
        return "4) Cliente (empresa):"

    # ---- STEP 4: cliente
    if step == 4:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cliente inválido. Escribe el nombre de la empresa."
        ticket["cliente"] = msg
        state["step"] = 5
        return "5) Nombre completo (mínimo 2 palabras):"

    # ---- STEP 5: nombre completo
    if step == 5:
        if not valid_fullname(msg):
            return "⚠️ Escribe nombre y apellido (mínimo 2 palabras)."
        ticket["nombre"] = msg
        # según tu JSON, contacto = nombre
        ticket["contacto"] = msg
        state["step"] = 6
        return "6) Cargo:"

    # ---- STEP 6: cargo
    if step == 6:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cargo inválido. Escribe tu cargo."
        ticket["cargo"] = msg
        state["step"] = 7
        return "7) Número de contacto (solo números, 7–15 dígitos):"

    # ---- STEP 7: número de contacto
    if step == 7:
        if not valid_phone(msg):
            return "⚠️ Número inválido. Solo dígitos (7–15)."
        # lo guardamos adicionalmente (aunque tu JSON ejemplo no lo trae)
        ticket["numero_contacto"] = msg
        state["step"] = 8
        return "8) ¿Tienes un número de caso previo? (sí/no)"

    # ---- STEP 8: caso previo (flag)
    if step == 8:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            state["step"] = 9
            return "Escribe el número del caso previo:"
        ticket["caso_previo"] = None
        # ✅ PRIORIDAD ES LO ÚLTIMO ANTES DEL RESUMEN
        state["step"] = 10
        return ask_priority_last(ticket)

    # ---- STEP 9: caso previo (valor)
    if step == 9:
        if not valid_nonempty(msg, 3):
            return "⚠️ Número de caso previo inválido."
        ticket["caso_previo"] = msg
        # ✅ PRIORIDAD ES LO ÚLTIMO ANTES DEL RESUMEN
        state["step"] = 10
        return ask_priority_last(ticket)

    # ---- STEP 10: confirmar prioridad sugerida por IA
    if step == 10:
        # el bot pregunta: "¿Confirmas prioridad X? (sí/no)"
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            # acepta prioridad sugerida
            ticket["prioridad"] = ticket["prioridad_sugerida"]
            state["step"] = 12
            return build_ticket_summary(ticket, include_priority=True) + "\n\n¿Confirmas crear el ticket? (sí/no)"
        # si no acepta, pide prioridad manual (y se valida con IA)
        state["step"] = 11
        return "Indica la prioridad (Alta/Media/Baja). La IA la validará:"

    # ---- STEP 11: prioridad manual + validación IA
    if step == 11:
        pr = msg.strip().capitalize()
        if pr.lower() not in PRIORIDADES:
            return "⚠️ Prioridad inválida. Usa: Alta, Media o Baja."

        # Validación IA
        try:
            verdict = ia_validar_prioridad(
                descripcion=ticket["descripcion"],
                tipo=ticket["tipo"],
                prioridad_usuario=pr
            )
        except Exception as e:
            print("⚠️ Error validando prioridad con IA:", e)
            verdict = {"valida": True, "prioridad_final": pr, "razon": "Validación IA no disponible"}

        ticket["prioridad_validacion"] = verdict
        ticket["prioridad"] = verdict.get("prioridad_final", pr)

        # Si IA dice que NO es válida, se lo explicamos y pedimos confirmación final
        if verdict.get("valida") is False:
            state["step"] = 10  # volvemos a confirmar (pero con prioridad corregida)
            ticket["prioridad_sugerida"] = ticket["prioridad"]
            return (
                "⚠️ La IA sugiere ajustar la prioridad.\n"
                f"Propuesta: {ticket['prioridad_sugerida']}\n"
                f"Motivo: {verdict.get('razon','')}\n\n"
                "¿Confirmas? (sí/no)"
            )

        # Si válida, seguimos
        state["step"] = 12
        return build_ticket_summary(ticket, include_priority=True) + "\n\n¿Confirmas crear el ticket? (sí/no)"

    # ---- STEP 12: confirmación final → generar ticket con tu JSON
    if step == 12:
        low = msg.lower()

        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."

        if low == "no":
            sessions.pop(user_id, None)
            return "❌ Ticket cancelado."


        # Construir ticket final
        final_ticket = {
            "tipo": ticket.get("tipo", "Software"),
            "titulo": ticket.get("titulo", ""),
            "descripcion": ticket.get("descripcion", ""),
            "nombre": ticket.get("nombre", ""),
            "estado": "Abierto",
            "cliente": ticket.get("cliente", ""),
            "prioridad": ticket.get("prioridad", "Media"),
            "contacto": ticket.get("contacto", ticket.get("nombre", "")),
            "cargo": ticket.get("cargo", "")
        }

        # Extras opcionales
        if "numero_contacto" in ticket:
            final_ticket["numero_contacto"] = ticket["numero_contacto"]

        final_ticket["caso_previo"] = ticket.get("caso_previo")

       # Guardar ticket usando helper reutilizable
        nuevo_ticket = crear_ticket_db(final_ticket, db)

        # Simular notificación
        notify(
            user_id,
            f"✅ Caso {nuevo_ticket.numero_caso} recibido. Estado: Abierto"
        )

        # Limpiar sesión
        sessions.pop(user_id, None)

        # Respuesta final
        return (
            "✅ Ticket creado correctamente.\n"
            f"🧾 Número de caso: {nuevo_ticket.numero_caso}\n"
            "📌 Estado: Abierto\n\n"
            "Te notificaré cambios de estado."
        )

    return "No entendí. Escribe tu problema o una pregunta (FAQ)."
    
def ask_priority_last(ticket: dict) -> str:
    """
    Este mensaje es lo último antes del resumen.
    Sugiere prioridad con IA y pide confirmación.
    """
    suger = ia_sugerir_prioridad(ticket.get("descripcion", ""), ticket.get("tipo", "Software"))
    ticket["prioridad_sugerida"] = suger["prioridad"]

    return (
        "✅ Antes del resumen, definamos la PRIORIDAD (validada por IA).\n"
        f"⚡ Prioridad sugerida: {ticket['prioridad_sugerida']}\n"
        "¿Confirmas? (sí/no)"
    )


def build_ticket_summary(ticket: dict, include_priority: bool = True) -> str:
    t = ticket or {}
    parts = [
        "📋 RESUMEN DEL TICKET",
        f"📝 Título: {t.get('titulo','')}",
        f"🏢 Cliente: {t.get('cliente','')}",
        f"📌 Tipo: {t.get('tipo','')}",
        f"🧩 Descripción: {t.get('descripcion','')}",
        f"👤 Nombre: {t.get('nombre','')}",
        f"💼 Cargo: {t.get('cargo','')}",
        f"📞 Número de contacto: {t.get('numero_contacto','')}",
        f"🔁 Caso previo: {t.get('caso_previo', None)}",
    ]
    if include_priority:
        parts.append(f"⚡ Prioridad: {t.get('prioridad', t.get('prioridad_sugerida',''))}")
    return "\n".join(parts)


# ---------------- ENDPOINTS ----------------

def crear_ticket_db(ticket_data: dict, db: Session):

    current_year = datetime.now().year

    nuevo_ticket = Ticket(
        titulo=ticket_data["titulo"],
        cliente=ticket_data["cliente"],
        tipo=ticket_data["tipo"],
        prioridad=ticket_data["prioridad"],
        descripcion=ticket_data["descripcion"],
        contacto=ticket_data["contacto"],
        nombre=ticket_data["nombre"],
        cargo=ticket_data["cargo"],
        estado="Abierto"
    )

    db.add(nuevo_ticket)
    db.commit()
    db.refresh(nuevo_ticket)

    nuevo_ticket.numero_caso = (
        f"NET-{current_year}-{nuevo_ticket.id:03d}"
    )

    db.commit()
    db.refresh(nuevo_ticket)

    return nuevo_ticket


@app.post("/chat")
def chat(data: ChatMessage, db: Session = Depends(get_db)):

    response = process_message(
        data.message,
        data.user_id,
        db
    )

    return {"response": response}

@app.get("/notifications/{user_id}")
def get_notifications(user_id: str):
    # Devuelve y limpia notificaciones (bandeja)
    msgs = notifications.get(user_id, [])
    notifications[user_id] = []
    return {"notifications": msgs}


@app.get("/ticket/{numero_caso}")
def get_ticket(numero_caso: str, db: Session = Depends(get_db)):

    ticket = db.query(Ticket).filter(
        Ticket.numero_caso == numero_caso
    ).first()

    if not ticket:
        return {
            "ok": False,
            "error": "Ticket no encontrado"
        }

    return {
        "ok": True,
        "ticket": ticket
    }

@app.post("/ticket/{numero_caso}/status")
def update_status(
    numero_caso: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db)
):

    estado = (payload.get("estado") or "").strip()
    user_id = (payload.get("user_id") or "").strip()

    if not estado:
        return {"ok": False, "error": "Falta estado"}

    if not user_id:
        return {"ok": False, "error": "Falta user_id"}

    # Buscar ticket en SQLite
    ticket = db.query(Ticket).filter(
        Ticket.numero_caso == numero_caso
    ).first()

    if not ticket:
        return {
            "ok": False,
            "error": "Ticket no encontrado"
        }

    # Actualizar estado
    ticket.estado = estado

    db.commit()
    db.refresh(ticket)

    # Notificación
    notify(
        user_id,
        f"🔔 Caso {numero_caso} actualizado. Estado: {estado}"
    )

    return {
        "ok": True,
        "numero_caso": numero_caso,
        "estado": estado
    }


@app.post("/handoff")
def handoff(payload: dict = Body(...)):
    """
    Devuelve el contexto completo para NV1 (handoff).
    payload: {"user_id": "1"}
    """
    user_id = (payload.get("user_id") or "").strip()
    current_ticket = sessions.get(user_id, {}).get("ticket", {})
    resumen = build_ticket_summary(current_ticket, include_priority=True)
    return {"ok": True, "context": current_ticket, "summary": resumen}


@app.get("/")
def root():
    return {"status": "ok"}

# -------- CRUD DE TICKETS --------

# Crear un nuevo ticket usando helper reutilizable
@app.post("/tickets")
def create_ticket(
    ticket: TicketCreate,
    db: Session = Depends(get_db)
):

    ticket_data = {
        "titulo": ticket.titulo,
        "cliente": ticket.cliente,
        "tipo": ticket.tipo,
        "prioridad": ticket.prioridad,
        "descripcion": ticket.descripcion,
        "contacto": ticket.contacto,
        "nombre": ticket.nombre,
        "cargo": ticket.cargo
    }

    nuevo_ticket = crear_ticket_db(ticket_data, db)

    return nuevo_ticket



# Obtener todos los tickets
@app.get("/tickets")
def get_tickets(db: Session = Depends(get_db)):

    tickets = db.query(Ticket).all()

    return tickets

# Obtener un ticket por ID
@app.get("/tickets/{ticket_id}")
def get_ticket_by_id(ticket_id: int, db: Session = Depends(get_db)):

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:
        return {"error": "Ticket no encontrado"}

    return ticket

# Actualizar el estado de un ticket
@app.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: int, estado: str, db: Session = Depends(get_db)):

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:
        return {"error": "Ticket no encontrado"}

    ticket.estado = estado

    db.commit()
    db.refresh(ticket)

    return ticket

# Eliminar un ticket
@app.delete("/tickets/{ticket_id}")
def delete_ticket(ticket_id: int, db: Session = Depends(get_db)):

    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()

    if not ticket:
        return {"error": "Ticket no encontrado"}

    db.delete(ticket)
    db.commit()

    return {"message": "Ticket eliminado"}
