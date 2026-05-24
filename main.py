from fastapi import FastAPI, Body, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from datetime import datetime
import json
import re
import requests
from typing import Optional, List

from database import engine, get_db
from models import Ticket
from schemas import TicketCreate, TicketResponse, ChatMessage, StatusUpdate
from ollama_client import analizar_ticket
from rag_engine import cargar_kb, buscar_respuesta

# =========================================================
# FastAPI App
# =========================================================

app = FastAPI(
    title="SupportSync API",
    description="Sistema inteligente de gestión de tickets",
    version="2.0.0"
)

# Crear tablas
from models import Base
Base.metadata.create_all(bind=engine)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================================================
# Configuration
# =========================================================

BOT_NAME = "NETMASK Assistant"
COMPANY_NAME = "NETMASK"

# =========================================================
# Load KB
# =========================================================

try:
    cargar_kb()
    print("✅ KB cargada correctamente")
except Exception as e:
    print("⚠️ Error cargando KB:", e)

# =========================================================
# In-memory state
# =========================================================

sessions = {}
notifications = {}

TIPOS_CATEGORIA = {"software", "hardware", "red", "acceso", "otro"}
PRIORIDADES = {"alta", "media", "baja"}

KW_IMPACTO_ALTO = {"toda", "todos", "empresa", "sede", "global", "general", "producción", "produccion"}
KW_CAIDA = {"caído", "caido", "sin servicio", "no hay servicio", "no funciona", "caida", "caída", "down"}
KW_SEGURIDAD = {"ransom", "ransomware", "phishing", "hack", "brecha", "virus", "malware", "intrusión", "intrusion"}

# =========================================================
# Helper Functions
# =========================================================

def notify(user_id: str, text: str):
    notifications.setdefault(user_id, []).append({
        "ts": datetime.utcnow().isoformat(),
        "text": text,
    })

def next_numero_caso(db: Session):
    current_year = datetime.now().year
    
    last_ticket = db.query(Ticket).filter(
        Ticket.numero_caso.like(f"NET-{current_year}-%")
    ).order_by(Ticket.id.desc()).first()
    
    if last_ticket:
        seq = int(last_ticket.numero_caso.split('-')[-1]) + 1
    else:
        seq = 1
    
    return f"NET-{current_year}-{seq:03d}"

def valid_nonempty(s: str, min_len: int = 2):
    return bool(s.strip()) and len(s.strip()) >= min_len

def valid_fullname(s: str):
    return len(s.strip().split()) >= 2

def valid_phone(s: str):
    return s.isdigit() and 7 <= len(s) <= 15

def build_ticket_summary(ticket: dict):
    return (
        "📋 RESUMEN DEL CASO\n\n"
        f"🧾 Caso: {ticket.get('numero_caso', '(pendiente)')}\n"
        f"🏢 Cliente: {ticket.get('cliente', '')}\n"
        f"📌 Tipo: {ticket.get('tipo', '')}\n"
        f"⚡ Prioridad: {ticket.get('prioridad', ticket.get('prioridad_sugerida', ''))}\n"
        f"📝 Título: {ticket.get('titulo', '')}\n"
        f"🧩 Descripción: {ticket.get('descripcion', '')}\n\n"
        f"👤 Contacto: {ticket.get('nombre', '')}\n"
        f"💼 Cargo: {ticket.get('cargo', '')}\n"
        f"📞 Teléfono: {ticket.get('numero_contacto', '')}\n"
        f"🔁 Caso previo: {ticket.get('caso_previo', 'No registra')}\n"
        f"📍 Estado: {ticket.get('estado', 'Abierto')}"
    )

def crear_ticket_en_db(ticket_data: dict, db: Session):
    numero_caso = next_numero_caso(db)
    
    db_ticket = Ticket(
        numero_caso=numero_caso,
        titulo=ticket_data.get("titulo", ""),
        cliente=ticket_data.get("cliente", ""),
        tipo=ticket_data.get("tipo", "Software"),
        prioridad=ticket_data.get("prioridad", "Media"),
        descripcion=ticket_data.get("descripcion", ""),
        contacto=ticket_data.get("contacto", ticket_data.get("nombre", "")),
        nombre=ticket_data.get("nombre", ""),
        cargo=ticket_data.get("cargo", ""),
        numero_contacto=ticket_data.get("numero_contacto"),
        caso_previo=ticket_data.get("caso_previo"),
        estado="Abierto"
    )
    
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    
    return db_ticket

# =========================================================
# Ollama Integration
# =========================================================

OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen3.5:0.8b"

def _limpiar_json(raw: str):
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else "{}"

def ia_validar_prioridad(descripcion, tipo, prioridad_usuario):
    prompt = (
        "Eres un analista de soporte técnico.\n\n"
        "Reglas:\n"
        "- Alta: caída total, incidente crítico o seguridad.\n"
        "- Media: degradación parcial.\n"
        "- Baja: solicitud simple.\n\n"
        f"Tipo: {tipo}\n"
        f"Descripción: {descripcion}\n"
        f"Prioridad propuesta: {prioridad_usuario}\n\n"
        'Devuelve SOLO este JSON:\n'
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
        return {"valida": True, "prioridad_final": prioridad_usuario, "razon": "No fue posible validar."}

def ia_sugerir_prioridad(descripcion, tipo):
    try:
        res = analizar_ticket(f"Tipo: {tipo}. Descripción: {descripcion}")
        pr = (res.get("prioridad") or "Media").strip().capitalize()
        if pr not in {"Alta", "Media", "Baja"}:
            pr = "Media"
        return pr
    except Exception:
        return "Media"

def is_technical(msg: str):
    keywords = ["cisco", "vpn", "fortinet", "ekahau", "no funciona", "error", "red", "internet", "caído", "caido", "anyconnect"]
    return any(k in msg.lower() for k in keywords)

# =========================================================
# Bot Logic
# =========================================================

def process_message(message: str, user_id: str, db: Session):
    msg = (message or "").strip()
    
    # RAG for technical questions
    if is_technical(msg):
        try:
            rag_answer = buscar_respuesta(msg)
            if rag_answer and "no tengo información" not in rag_answer.lower():
                return (
                    "📘 Posible solución encontrada:\n\n"
                    f"{rag_answer}\n\n"
                    "Si no te sirve, escribe 'crear ticket'."
                )
        except Exception as e:
            print("⚠️ RAG error:", e)
    
    # Quick actions
    if msg.lower() == "crear ticket":
        sessions[user_id] = {"step": 1, "ticket": {}, "errores": 0}
        return (
            f"Voy a ayudarte a registrar un caso de soporte.\n\n"
            "Por favor describe el inconveniente que estás presentando."
        )
    
    if msg.lower() == "consultar ticket":
        return "🧾 Por favor escribe el número de caso.\n\nEjemplo: NET-2026-001"
    
    # Query ticket
    if re.match(r"^NET-\d{4}-\d{3}$", msg.upper()):
        numero = msg.upper()
        ticket = db.query(Ticket).filter(Ticket.numero_caso == numero).first()
        
        if not ticket:
            return "⚠️ No encontré un ticket con ese número."
        
        return (
            "📋 INFORMACIÓN DEL CASO\n\n"
            f"🧾 Caso: {ticket.numero_caso}\n"
            f"📌 Tipo: {ticket.tipo}\n"
            f"⚡ Prioridad: {ticket.prioridad}\n"
            f"📍 Estado: {ticket.estado}\n"
            f"📝 Título: {ticket.titulo}"
        )
    
    # FAQ
    if msg.lower().startswith("faq:") or "?" in msg:
        pregunta = msg[4:].strip() if msg.lower().startswith("faq:") else msg
        if pregunta:
            respuesta = buscar_respuesta(pregunta)
            return "📘 Encontré información relacionada:\n\n" + respuesta
    
    # Initialize session
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}, "errores": 0}
    
    state = sessions[user_id]
    step = state["step"]
    ticket = state["ticket"]
    
    # Step-by-step ticket creation
    if step == 0:
        state["step"] = 1
        return f"👋 Hola, soy {BOT_NAME}. Describe el problema que tienes."
    
    if step == 1:
        if len(msg) < 10:
            return "📝 Describe el problema con más detalle (mínimo 10 caracteres)."
        ticket["descripcion"] = msg
        state["step"] = 2
        return "¿Qué tipo de problema? (Software/Hardware/Red/Acceso/Otro)"
    
    if step == 2:
        if msg.lower() not in TIPOS_CATEGORIA:
            return "Tipo inválido. Usa: Software, Hardware, Red, Acceso u Otro."
        ticket["tipo"] = msg.capitalize()
        state["step"] = 3
        return "Escribe un título corto para el caso (5-80 caracteres)."
    
    if step == 3:
        if not (5 <= len(msg) <= 80):
            return "El título debe tener entre 5 y 80 caracteres."
        ticket["titulo"] = msg
        state["step"] = 4
        return "Nombre de la empresa o cliente:"
    
    if step == 4:
        if not valid_nonempty(msg, 2):
            return "Ingresa un nombre válido."
        ticket["cliente"] = msg
        state["step"] = 5
        return "Tu nombre completo:"
    
    if step == 5:
        if not valid_fullname(msg):
            return "Ingresa nombre y apellido."
        ticket["nombre"] = msg
        ticket["contacto"] = msg
        state["step"] = 6
        return "Tu cargo:"
    
    if step == 6:
        if not valid_nonempty(msg, 2):
            return "Cargo inválido."
        ticket["cargo"] = msg
        state["step"] = 7
        return "Número de contacto (solo números, 7-15 dígitos):"
    
    if step == 7:
        if not valid_phone(msg):
            return "Número inválido."
        ticket["numero_contacto"] = msg
        state["step"] = 8
        return "¿Tienes un caso previo? (sí/no)"
    
    if step == 8:
        if msg.lower() not in {"sí", "si", "no"}:
            return "Responde sí o no."
        if msg.lower() in {"sí", "si"}:
            state["step"] = 9
            return "Ingresa el número del caso previo:"
        ticket["caso_previo"] = None
        state["step"] = 10
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"Prioridad sugerida: {pr}\n¿Confirmas? (sí/no)"
    
    if step == 9:
        ticket["caso_previo"] = msg
        state["step"] = 10
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"Prioridad sugerida: {pr}\n¿Confirmas? (sí/no)"
    
    if step == 10:
        if msg.lower() not in {"sí", "si", "no"}:
            return "Responde sí o no."
        if msg.lower() in {"sí", "si"}:
            ticket["prioridad"] = ticket.get("prioridad_sugerida", "Media")
            state["step"] = 12
        else:
            state["step"] = 11
            return "Indica prioridad: Alta, Media o Baja."
    
    if step == 11:
        pr = msg.strip().capitalize()
        if pr.lower() not in PRIORIDADES:
            return "Prioridad inválida."
        verdict = ia_validar_prioridad(ticket["descripcion"], ticket["tipo"], pr)
        ticket["prioridad"] = verdict.get("prioridad_final", pr)
        state["step"] = 12
    
    if step == 12:
        state["step"] = 13
        return build_ticket_summary(ticket) + "\n\n¿Crear ticket? (sí/no)"
    
    if step == 13:
        if msg.lower() not in {"sí", "si", "no"}:
            return "Responde sí o no."
        if msg.lower() == "no":
            sessions.pop(user_id, None)
            return "Ticket cancelado."
        
        ticket_data = {
            "titulo": ticket.get("titulo", ""),
            "cliente": ticket.get("cliente", ""),
            "tipo": ticket.get("tipo", "Software"),
            "prioridad": ticket.get("prioridad", "Media"),
            "descripcion": ticket.get("descripcion", ""),
            "contacto": ticket.get("contacto", ticket.get("nombre", "")),
            "nombre": ticket.get("nombre", ""),
            "cargo": ticket.get("cargo", ""),
            "numero_contacto": ticket.get("numero_contacto"),
            "caso_previo": ticket.get("caso_previo"),
        }
        
        nuevo_ticket = crear_ticket_en_db(ticket_data, db)
        notify(user_id, f"✅ Caso {nuevo_ticket.numero_caso} creado")
        sessions.pop(user_id, None)
        
        return (
            f"✅ Ticket creado exitosamente.\n"
            f"🧾 Número: {nuevo_ticket.numero_caso}\n"
            f"Gracias por contactar {COMPANY_NAME} 💙"
        )
    
    return "No entendí. Escribe 'crear ticket' para comenzar."

# =========================================================
# API Endpoints
# =========================================================

@app.post("/chat")
def chat(data: ChatMessage, db: Session = Depends(get_db)):
    response = process_message(data.message, data.user_id, db)
    return {"response": response}

@app.get("/notifications/{user_id}")
def get_notifications(user_id: str):
    msgs = notifications.get(user_id, [])
    notifications[user_id] = []
    return {"notifications": msgs}

@app.get("/ticket/{numero_caso}")
def get_ticket(numero_caso: str, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.numero_caso == numero_caso).first()
    if not ticket:
        return {"ok": False, "error": "Ticket no existe"}
    return {"ok": True, "ticket": ticket}

# =========================================================
# CRUD Endpoints
# =========================================================

@app.post("/tickets", response_model=TicketResponse)
def create_ticket(ticket: TicketCreate, db: Session = Depends(get_db)):
    ticket_data = ticket.model_dump()
    nuevo_ticket = crear_ticket_en_db(ticket_data, db)
    return nuevo_ticket

@app.get("/tickets", response_model=List[TicketResponse])
def get_tickets(skip: int = 0, limit: int = 100, estado: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Ticket)
    if estado:
        query = query.filter(Ticket.estado == estado)
    return query.offset(skip).limit(limit).all()

@app.get("/tickets/{ticket_id}", response_model=TicketResponse)
def get_ticket_by_id(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    return ticket

@app.put("/tickets/{ticket_id}", response_model=TicketResponse)
def update_ticket_status(ticket_id: int, status_update: StatusUpdate, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    ticket.estado = status_update.estado
    ticket.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(ticket)
    
    notify(status_update.user_id, f"🔔 Caso {ticket.numero_caso} actualizado: {status_update.estado}")
    return ticket

@app.delete("/tickets/{ticket_id}")
def delete_ticket(ticket_id: int, db: Session = Depends(get_db)):
    ticket = db.query(Ticket).filter(Ticket.id == ticket_id).first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    db.delete(ticket)
    db.commit()
    return {"message": "Ticket eliminado", "ticket_id": ticket_id}

@app.get("/tickets/stats/summary")
def get_tickets_summary(db: Session = Depends(get_db)):
    return {
        "total": db.query(Ticket).count(),
        "abiertos": db.query(Ticket).filter(Ticket.estado == "Abierto").count(),
        "en_proceso": db.query(Ticket).filter(Ticket.estado == "En Proceso").count(),
        "cerrados": db.query(Ticket).filter(Ticket.estado == "Cerrado").count(),
        "prioridad_alta": db.query(Ticket).filter(Ticket.prioridad == "Alta").count(),
        "prioridad_media": db.query(Ticket).filter(Ticket.prioridad == "Media").count(),
        "prioridad_baja": db.query(Ticket).filter(Ticket.prioridad == "Baja").count()
    }

@app.get("/")
def root():
    return {"status": "ok", "message": f"{BOT_NAME} funcionando"}