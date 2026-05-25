from fastapi import FastAPI, Body, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
 
import json
import re
import requests
from datetime import datetime
 
from database import engine, get_db
import models
from ollama_client import analizar_ticket
from rag_engine import (
    cargar_kb, buscar_respuesta,
    guardar_solucion, buscar_solucion_previa,
    MARCAS_SOPORTADAS,
)
 
models.Base.metadata.create_all(bind=engine)
 
app = FastAPI()
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
try:
    cargar_kb()
except Exception as e:
    print("⚠️ No se pudo cargar la KB (RAG):", e)
 
 
# ── Modelos API ────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    user_id: str
    message: str
 
 
# ── Estado en memoria ──────────────────────────────────────────────────────────
sessions      = {}
tickets       = {}
notifications = {}
 
COUNTERS = {"id": 0, "year": datetime.now().year, "seq": 0}
 
TIPOS_CATEGORIA = {"software", "hardware", "red", "acceso", "otro"}
PRIORIDADES     = {"alta", "media", "baja"}
MARCAS_OPCIONES = MARCAS_SOPORTADAS | {"otro"}
 
KW_IMPACTO_ALTO = {"toda", "todos", "nadie", "empresa", "sede", "global", "producción", "produccion"}
KW_CAIDA        = {"caído", "caido", "sin servicio", "no hay servicio", "caida", "caída", "down"}
KW_SEGURIDAD    = {"ransom", "ransomware", "phishing", "hack", "brecha", "virus", "malware", "intrusión"}
 
OLLAMA_GENERATE_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL        = "qwen3.5:0.8b"
 
 
# ── Helpers generales ──────────────────────────────────────────────────────────
def notify(user_id: str, text: str):
    notifications.setdefault(user_id, []).append(
        {"ts": datetime.utcnow().isoformat(), "text": text}
    )
 
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
 
def _limpiar_json(raw: str) -> str:
    raw   = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else "{}"
 
def build_ticket_summary(ticket: dict) -> str:
    t = ticket or {}
    return (
        f"📋 RESUMEN DEL TICKET\n"
        f"🧾 Número de caso  : {t.get('numero_caso', '(sin asignar)')}\n"
        f"📌 Tipo            : {t.get('tipo', '')}\n"
        f"🔌 Marca/Fabricante: {t.get('marca', 'No especificada').capitalize()}\n"
        f"📝 Título          : {t.get('titulo', '')}\n"
        f"🏢 Cliente         : {t.get('cliente', '')}\n"
        f"🧩 Descripción     : {t.get('descripcion', '')}\n"
        f"👤 Nombre          : {t.get('nombre', '')}\n"
        f"💼 Cargo           : {t.get('cargo', '')}\n"
        f"📞 Contacto        : {t.get('numero_contacto', '')}\n"
        f"🔁 Caso previo     : {t.get('caso_previo', 'No')}\n"
        f"⚡ Prioridad       : {t.get('prioridad', '')}\n"
        f"📍 Estado          : {t.get('estado', 'Abierto')}"
    )
 
 
# ── IA: generar sugerencia contextual desde KB ─────────────────────────────────
def ia_generar_sugerencia(descripcion: str, marca: str, kb_context: str) -> str:
    """
    Usa Ollama para sintetizar una sugerencia de solución en lenguaje natural,
    basada en el problema del usuario y el fragmento de documentación oficial.
    Si Ollama falla, devuelve el fragmento original formateado.
    """
    prompt = (
        f"Eres un técnico experto en soporte IT especializado en equipos {marca.capitalize()}.\n\n"
        f"Un cliente reporta el siguiente problema:\n\"{descripcion}\"\n\n"
        f"La documentación oficial de {marca.capitalize()} indica lo siguiente:\n"
        f"---\n{kb_context}\n---\n\n"
        "Con base en esa documentación, redacta una sugerencia de solución clara, "
        "paso a paso, dirigida al cliente. Sé conciso (máximo 5 pasos). "
        "No inventes información que no esté en la documentación. "
        "Responde SOLO con la sugerencia, sin saludos ni explicaciones adicionales."
    )
 
    try:
        r = requests.post(
            OLLAMA_GENERATE_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 300, "num_ctx": 2048},
                "keep_alive": "0",
            },
            timeout=90,
        )
        r.raise_for_status()
        raw = r.json().get("response", "").strip()
        # Limpiar posibles bloques <think> del modelo
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        return raw if len(raw) > 20 else kb_context
    except Exception:
        return kb_context
 
 
# ── IA: validar y sugerir prioridad ───────────────────────────────────────────
def ia_validar_prioridad(descripcion: str, tipo: str, prioridad_usuario: str) -> dict:
    prompt = (
        "Eres un analista de mesa de ayuda. Valida la PRIORIDAD del ticket.\n\n"
        "Reglas:\n"
        "- Alta: caída total, operación detenida, incidente crítico, seguridad.\n"
        "- Media: falla parcial o degradación importante.\n"
        "- Baja: solicitud, consulta, cambio no urgente.\n\n"
        f"Tipo: {tipo}\nDescripción: {descripcion}\nPrioridad propuesta: {prioridad_usuario}\n\n"
        'Devuelve SOLO JSON: {"valida": true, "prioridad_final": "Alta|Media|Baja", "razon": "..."}'
    )
    try:
        r = requests.post(OLLAMA_GENERATE_URL, json={
            "model": OLLAMA_MODEL, "prompt": prompt, "stream": False,
            "options": {"temperature": 0, "num_predict": 170, "num_ctx": 2048},
            "keep_alive": "0",
        }, timeout=60)
        r.raise_for_status()
        return json.loads(_limpiar_json(r.json().get("response", "{}")))
    except Exception:
        return {"valida": True, "prioridad_final": prioridad_usuario,
                "razon": "No se pudo validar con IA."}
 
def ia_sugerir_prioridad(descripcion: str, tipo: str) -> str:
    try:
        res = analizar_ticket(f"Tipo: {tipo}. Descripción: {descripcion}")
        pr  = (res.get("prioridad") or "Media").strip().capitalize()
        return pr if pr in {"Alta", "Media", "Baja"} else "Media"
    except Exception:
        return "Media"
 
 
# ── Ecuación de escalamiento ───────────────────────────────────────────────────
def calcular_score_escalamiento(ticket: dict, rag_sin_fuente=False, usuario_pidio_agente=False) -> dict:
    desc      = (ticket.get("descripcion") or "").lower()
    prioridad = (ticket.get("prioridad") or ticket.get("prioridad_sugerida") or "Media").capitalize()
 
    P = 100 if prioridad == "Alta" else 60 if prioridad == "Media" else 20
    I = 90 if any(k in desc for k in KW_CAIDA) else 80 if any(k in desc for k in KW_IMPACTO_ALTO) else 20
    S = 100 if any(k in desc for k in KW_SEGURIDAD) else 0
    genericos = {"no funciona", "no sirve", "ayuda", "urgente"}
    A = 80 if len(desc) < 20 else (70 if any(g in desc for g in genericos) and len(desc) < 35 else 0)
    R = 30 if ticket.get("caso_previo") else 0
    K = 100 if usuario_pidio_agente else (70 if rag_sin_fuente else 0)
 
    score    = round(0.25*P + 0.30*I + 0.20*S + 0.10*A + 0.05*R + 0.10*K)
    decision = "ESCALAR_YA" if score >= 70 else ("RECOMENDAR_ESCALAR" if score >= 50 else "NO_ESCALAR")
 
    razones = []
    if prioridad == "Alta":      razones.append("prioridad alta")
    if I >= 80:                  razones.append("impacto alto o caída")
    if S == 100:                 razones.append("posible incidente de seguridad")
    if A >= 70:                  razones.append("descripción ambigua")
    if R > 0:                    razones.append("hay caso previo")
    if rag_sin_fuente:           razones.append("sin respaldo documental")
    if usuario_pidio_agente:     razones.append("usuario pidió agente")
 
    return {"score": score, "decision": decision, "razones": razones}
 
 
# ── Bot conversacional ─────────────────────────────────────────────────────────
# Flujo de pasos:
#  0   → saludo
#  1   → descripción del problema
#  2   → marca del fabricante
#  2.1 → (interno) genera sugerencia con IA + KB → muestra al usuario
#  3   → ¿la sugerencia resolvió el problema? (sí/no)
#  3.1 → si no resolvió: ¿quiere intentar otro paso? (sí/no)
#  4   → tipo (Software/Hardware/Red/Acceso/Otro)
#  5   → título
#  6   → cliente
#  7   → nombre completo
#  8   → cargo
#  9   → teléfono
#  10  → caso previo (sí/no)
#  11  → número de caso previo
#  12  → confirmar prioridad sugerida por IA
#  13  → prioridad manual + validación IA
#  14  → score + resumen + confirmación
#  999 → decisión escalamiento a NV1
#  15  → confirmación final → crear ticket
 
def process_message(message: str, user_id: str, db: Session) -> str:
    msg = (message or "").strip()
 
    # ── FAQ / RAG directo ──────────────────────────────────────────────────────
    if msg.lower().startswith("faq:") or "?" in msg:
        pregunta     = msg[4:].strip() if msg.lower().startswith("faq:") else msg
        if not pregunta:
            return "⚠️ Escribe una pregunta válida."
        marca_sesion = sessions.get(user_id, {}).get("ticket", {}).get("marca")
        kb_raw       = buscar_respuesta(pregunta, marca=marca_sesion)
        sin_fuente   = "No encontré" in kb_raw
 
        if user_id not in sessions:
            sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": False}
        sessions[user_id]["rag_sin_fuente"] = sin_fuente
 
        if sin_fuente:
            return kb_raw + "\n\nEscribe 'hablar con agente' si necesitas soporte humano."
 
        # Sintetizar respuesta con IA
        marca_faq   = marca_sesion or "el fabricante"
        desc_faq    = sessions[user_id].get("ticket", {}).get("descripcion", pregunta)
        sugerencia  = ia_generar_sugerencia(desc_faq, marca_faq, kb_raw)
        return (
            f"💡 Sugerencia basada en documentación oficial:\n\n{sugerencia}\n\n"
            "Escribe 'hablar con agente' si necesitas soporte humano."
        )
 
    # ── Solicitud explícita de agente ──────────────────────────────────────────
    if msg.lower() in {"hablar con agente", "agente", "nv1", "escalar"}:
        if user_id not in sessions:
            sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": True}
        else:
            sessions[user_id]["usuario_pidio_agente"] = True
        t = sessions[user_id].get("ticket", {})
        return "✅ Te transfiero a NV1 con el contexto.\n\n" + build_ticket_summary(t)
 
    # ── Inicializar sesión ─────────────────────────────────────────────────────
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}, "rag_sin_fuente": False, "usuario_pidio_agente": False}
 
    state  = sessions[user_id]
    step   = state["step"]
    ticket = state["ticket"]
 
    # STEP 0 ── saludo
    if step == 0:
        state["step"] = 1
        return "Hola 👋 Describe el problema (mínimo 10 caracteres)."
 
    # STEP 1 ── descripción
    if step == 1:
        if len(msg) < 10:
            return "⚠️ Describe un poco más el problema (mínimo 10 caracteres)."
        ticket["descripcion"] = msg
        state["step"] = 2
        marcas_str = ", ".join(sorted(MARCAS_SOPORTADAS)).title()
        return f"¿Cuál es la marca o fabricante del equipo involucrado?\n({marcas_str}, Otro)"
 
    # STEP 2 ── marca → buscar KB → generar sugerencia con IA
    if step == 2:
        marca = msg.strip().lower()
        if marca not in MARCAS_OPCIONES:
            return f"⚠️ Marca no reconocida. Opciones: {', '.join(sorted(MARCAS_OPCIONES)).title()}."
        ticket["marca"] = marca
 
        # 1. Buscar solución aprendida de casos previos
        solucion_previa = buscar_solucion_previa(db, marca, ticket["descripcion"])
        if solucion_previa:
            state["kb_respuesta"] = solucion_previa
            state["sugerencia_origen"] = "caso_previo"
            state["step"] = 3
            return (
                f"🗂️ Encontré una solución de un caso anterior con equipos {marca.capitalize()}:\n\n"
                f"{solucion_previa}\n\n"
                "─────────────────────────────\n"
                "¿Esta sugerencia resuelve tu problema? (sí/no)"
            )
 
        # 2. Buscar en KB oficial del fabricante
        kb_raw     = buscar_respuesta(ticket["descripcion"], marca=marca)
        sin_fuente = "No encontré" in kb_raw
        state["rag_sin_fuente"] = sin_fuente
 
        if not sin_fuente:
            # 3. Usar IA para sintetizar sugerencia en lenguaje natural
            sugerencia = ia_generar_sugerencia(
                descripcion=ticket["descripcion"],
                marca=marca,
                kb_context=kb_raw,
            )
            state["kb_respuesta"]      = sugerencia
            state["kb_raw"]            = kb_raw
            state["sugerencia_origen"] = "kb_oficial"
            state["step"] = 3
            return (
                f"🔍 Revisé la documentación oficial de {marca.capitalize()} "
                f"y encontré lo siguiente para tu caso:\n\n"
                f"💡 Sugerencia de solución:\n{sugerencia}\n\n"
                "─────────────────────────────\n"
                "¿Esta sugerencia resuelve tu problema? (sí/no)"
            )
 
        # Sin resultados en KB → continuar con ticket
        state["step"] = 4
        return (
            f"ℹ️ No encontré documentación específica para {marca.capitalize()} "
            "relacionada con tu problema.\n\n"
            "Continuemos con el ticket para escalar a soporte.\n\n"
            "¿Cuál es el tipo de incidencia? (Software / Hardware / Red / Acceso / Otro)"
        )
 
    # STEP 3 ── ¿la sugerencia resolvió el problema?
    if step == 3:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
 
        if low in {"sí", "si"}:
            # Guardar como solución aprendida (solo si vino de KB oficial, no de caso previo)
            if state.get("sugerencia_origen") == "kb_oficial":
                try:
                    guardar_solucion(
                        db,
                        marca=ticket.get("marca", "otro"),
                        error_desc=ticket["descripcion"],
                        solucion=state.get("kb_respuesta", ""),
                        numero_caso=None,
                    )
                except Exception:
                    pass
            sessions.pop(user_id, None)
            return (
                "✅ ¡Excelente! Me alegra que se haya resuelto.\n\n"
                "La solución quedó registrada para casos similares en el futuro. "
                "Si tienes otro problema, escríbeme."
            )
 
        # No resolvió → ofrecer pasos adicionales antes de crear ticket
        state["step"] = 3.5
        return (
            "Entendido, la sugerencia no fue suficiente.\n\n"
            "¿Quieres que intente con pasos adicionales de diagnóstico "
            "antes de crear el ticket? (sí/no)"
        )
 
    # STEP 3.5 ── diagnóstico adicional o ir directo al ticket
    if step == 3.5:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
 
        if low in {"sí", "si"}:
            # Generar pasos adicionales de diagnóstico con IA
            marca      = ticket.get("marca", "otro")
            descripcion = ticket["descripcion"]
            sugerencia_previa = state.get("kb_respuesta", "")
 
            prompt_diag = (
                f"El cliente tiene un problema con equipos {marca.capitalize()}: \"{descripcion}\".\n"
                f"Ya se intentó la siguiente solución sin éxito:\n{sugerencia_previa}\n\n"
                "Proporciona 3 pasos adicionales de diagnóstico avanzado para este problema. "
                "Sé específico y técnico. Responde SOLO con los pasos numerados."
            )
            try:
                r = requests.post(OLLAMA_GENERATE_URL, json={
                    "model": OLLAMA_MODEL, "prompt": prompt_diag, "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 300, "num_ctx": 2048},
                    "keep_alive": "0",
                }, timeout=90)
                r.raise_for_status()
                pasos = r.json().get("response", "").strip()
                pasos = re.sub(r"<think>.*?</think>", "", pasos, flags=re.DOTALL).strip()
            except Exception:
                pasos = "No pude generar pasos adicionales en este momento."
 
            state["step"] = 3.8
            return (
                f"🔧 Pasos adicionales de diagnóstico para {marca.capitalize()}:\n\n"
                f"{pasos}\n\n"
                "─────────────────────────────\n"
                "¿Alguno de estos pasos resolvió el problema? (sí/no)"
            )
 
        # No quiere más diagnóstico → ir al ticket
        state["step"] = 4
        return "De acuerdo, creamos el ticket.\n\n¿Cuál es el tipo? (Software / Hardware / Red / Acceso / Otro)"
 
    # STEP 3.8 ── ¿los pasos adicionales resolvieron?
    if step == 3.8:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            sessions.pop(user_id, None)
            return "✅ ¡Perfecto! Problema resuelto con diagnóstico avanzado. Escríbeme si necesitas algo más."
        state["step"] = 4
        return (
            "Entendido, escalaremos el caso a soporte técnico.\n\n"
            "¿Cuál es el tipo de incidencia? (Software / Hardware / Red / Acceso / Otro)"
        )
 
    # STEP 4 ── tipo
    if step == 4:
        t = msg.lower()
        if t not in TIPOS_CATEGORIA:
            return "⚠️ Tipo inválido. Usa: Software, Hardware, Red, Acceso u Otro."
        ticket["tipo"] = t.capitalize()
        state["step"]  = 5
        return "Título del caso (5–80 caracteres):"
 
    # STEP 5 ── título
    if step == 5:
        if not (5 <= len(msg) <= 80):
            return "⚠️ El título debe tener entre 5 y 80 caracteres."
        ticket["titulo"] = msg
        state["step"]    = 6
        return "Cliente (empresa):"
 
    # STEP 6 ── cliente
    if step == 6:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cliente inválido."
        ticket["cliente"] = msg
        state["step"]     = 7
        return "Nombre completo (mínimo 2 palabras):"
 
    # STEP 7 ── nombre
    if step == 7:
        if not valid_fullname(msg):
            return "⚠️ Escribe nombre y apellido (mínimo 2 palabras)."
        ticket["nombre"]   = msg
        ticket["contacto"] = msg
        state["step"]      = 8
        return "Cargo:"
 
    # STEP 8 ── cargo
    if step == 8:
        if not valid_nonempty(msg, 2):
            return "⚠️ Cargo inválido."
        ticket["cargo"] = msg
        state["step"]   = 9
        return "Número de contacto (solo números, 7–15 dígitos):"
 
    # STEP 9 ── teléfono
    if step == 9:
        if not valid_phone(msg):
            return "⚠️ Número inválido. Solo dígitos (7–15)."
        ticket["numero_contacto"] = msg
        state["step"]             = 10
        return "¿Tienes un número de caso previo? (sí/no)"
 
    # STEP 10 ── caso previo flag
    if step == 10:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            state["step"] = 11
            return "Ingresa el número del caso previo:"
        ticket["caso_previo"] = None
        state["step"]         = 12
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"✅ Prioridad sugerida por IA: {pr}. ¿Confirmas? (sí/no)"
 
    # STEP 11 ── caso previo valor
    if step == 11:
        if not valid_nonempty(msg, 3):
            return "⚠️ Número de caso previo inválido."
        ticket["caso_previo"] = msg
        state["step"]         = 12
        pr = ia_sugerir_prioridad(ticket["descripcion"], ticket["tipo"])
        ticket["prioridad_sugerida"] = pr
        return f"✅ Prioridad sugerida por IA: {pr}. ¿Confirmas? (sí/no)"
 
    # STEP 12 ── confirmar prioridad sugerida
    if step == 12:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            ticket["prioridad"] = ticket.get("prioridad_sugerida", "Media")
            state["step"] = 14
            step = 14
        else:
            state["step"] = 13
            return "Indica prioridad (Alta / Media / Baja). La IA la validará:"
 
    # STEP 13 ── prioridad manual + validación IA
    if step == 13:
        pr = msg.strip().capitalize()
        if pr.lower() not in PRIORIDADES:
            return "⚠️ Prioridad inválida. Usa: Alta, Media o Baja."
        verdict = ia_validar_prioridad(ticket["descripcion"], ticket["tipo"], pr)
        ticket["prioridad_validacion"] = verdict
        ticket["prioridad"]            = verdict.get("prioridad_final", pr)
        state["step"] = 14
        step = 14 
 
    # STEP 14 ── score + resumen + confirmación
    if step == 14:
        if "numero_caso" not in ticket:
            ticket["numero_caso"] = next_numero_caso()
        ticket.setdefault("estado", "Abierto")
 
        esc = calcular_score_escalamiento(
            ticket,
            rag_sin_fuente=bool(state.get("rag_sin_fuente")),
            usuario_pidio_agente=bool(state.get("usuario_pidio_agente")),
        )
        ticket["escalamiento"] = esc
        razones_str = ", ".join(esc["razones"]) if esc["razones"] else "sin señales adicionales"
 
        if esc["decision"] == "ESCALAR_YA":
            state["step"] = 999
            return (
                f"🚨 Este caso parece crítico.\n"
                f"📈 Score: {esc['score']}/100\n"
                f"Motivos: {razones_str}\n\n"
                "¿Quieres conectarte con un agente humano (NV1) ahora? (sí/no)"
            )
 
        state["step"] = 15
        recomendacion = (
            "⚠️ Considera escalar a NV1."
            if esc["decision"] == "RECOMENDAR_ESCALAR"
            else "✅ Puede gestionarse sin agente."
        )
        return (
            build_ticket_summary(ticket)
            + f"\n\n📈 Score: {esc['score']}/100 — {recomendacion}\n"
            + f"Motivos: {razones_str}\n\n"
            + "¿Confirmas crear el ticket? (sí/no)"
        )
 
    # STEP 999 ── decisión escalamiento
    if step == 999:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low in {"sí", "si"}:
            notify(user_id, f"🔔 Caso {ticket.get('numero_caso')} escalado a NV1")
            state["step"] = 15
            return "✅ Conectando con NV1...\n\n📦 Contexto:\n" + build_ticket_summary(ticket)
        state["step"] = 15
        return build_ticket_summary(ticket) + "\n\n¿Confirmas crear el ticket? (sí/no)"
 
    # STEP 15 ── confirmación final → crear ticket
    if step == 15:
        low = msg.lower()
        if low not in {"sí", "si", "no"}:
            return "⚠️ Responde 'sí' o 'no'."
        if low == "no":
            sessions.pop(user_id, None)
            return "❌ Ticket cancelado."
 
        numero_caso  = ticket.get("numero_caso") or next_numero_caso()
        final_ticket = {
            "tipo"           : ticket.get("tipo", "Software"),
            "numero_caso"    : numero_caso,
            "titulo"         : ticket.get("titulo", ""),
            "descripcion"    : ticket.get("descripcion", ""),
            "nombre"         : ticket.get("nombre", ""),
            "estado"         : ticket.get("estado", "Abierto"),
            "cliente"        : ticket.get("cliente", ""),
            "user_id"        : user_id,
            "id"             : next_id(),
            "prioridad"      : ticket.get("prioridad", "Media"),
            "contacto"       : ticket.get("contacto", ticket.get("nombre", "")),
            "cargo"          : ticket.get("cargo", ""),
            "marca"          : ticket.get("marca", "otro"),
            "numero_contacto": ticket.get("numero_contacto"),
            "caso_previo"    : ticket.get("caso_previo"),
            "escalamiento"   : ticket.get("escalamiento"),
            "created_at"     : datetime.utcnow().isoformat(),  # ← agregar esto
            "updated_at"     : datetime.utcnow().isoformat(),  # ← y esto
        }
        tickets[numero_caso] = final_ticket
        notify(user_id, f"✅ Caso {numero_caso} creado.")
 
        # Guardar solución KB en historial
        kb_resp = state.get("kb_respuesta")
        if kb_resp and state.get("sugerencia_origen") == "kb_oficial":
            try:
                guardar_solucion(
                    db,
                    marca=final_ticket["marca"],
                    error_desc=ticket["descripcion"],
                    solucion=kb_resp,
                    numero_caso=numero_caso,
                )
            except Exception:
                pass
 
        sessions.pop(user_id, None)
 
        esc   = final_ticket.get("escalamiento", {})
        extra = (
            "\n🚨 Escribe 'hablar con agente' para escalar a NV1."
            if esc.get("decision") == "ESCALAR_YA" else ""
        )
        return f"✅ Ticket creado. Número de caso: {numero_caso}{extra}"
 
    return "No entendí. Escribe tu problema o una pregunta (FAQ)."
 
 
# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/chat")
def chat(data: ChatMessage, db: Session = Depends(get_db)):
    return {"response": process_message(data.message, data.user_id, db)}

@app.get("/tickets")
def get_tickets(limit: int = 100):
    lista = list(tickets.values())
    return lista[:limit]  
 
@app.get("/notifications/{user_id}")
def get_notifications(user_id: str):
    return {"notifications": notifications.pop(user_id, [])}
 
@app.get("/ticket/{numero_caso}")
def get_ticket(numero_caso: str):
    if numero_caso not in tickets:
        return {"ok": False, "error": "numero_caso no existe"}
    return {"ok": True, "ticket": tickets[numero_caso]}
 
@app.post("/ticket/{numero_caso}/status")
def update_status(numero_caso: str, payload: dict = Body(...)):
    if numero_caso not in tickets:
        return {"ok": False, "error": "numero_caso no existe"}
    estado  = (payload.get("estado") or "").strip()
    user_id = (payload.get("user_id") or "").strip()
    if not estado or not user_id:
        return {"ok": False, "error": "Faltan campos"}
    tickets[numero_caso]["estado"] = estado
    notify(user_id, f"🔔 Caso {numero_caso} → {estado}")
    return {"ok": True, "numero_caso": numero_caso, "estado": estado}
 
@app.post("/handoff")
def handoff(payload: dict = Body(...)):
    user_id = (payload.get("user_id") or "").strip()
    return {"ok": True, "context": sessions.get(user_id, {}).get("ticket", {})}
 
@app.get("/kb/soluciones")
def listar_soluciones(marca: str = None, db: Session = Depends(get_db)):
    from models import SolucionKB
    q = db.query(SolucionKB)
    if marca:
        q = q.filter(SolucionKB.marca == marca.lower())
    return {"soluciones": [
        {"id": s.id, "marca": s.marca, "error": s.error_desc,
         "solucion": s.solucion, "caso": s.numero_caso,
         "veces_usado": s.veces_usado, "creado_en": str(s.creado_en)}
        for s in q.order_by(SolucionKB.veces_usado.desc()).limit(50).all()
    ]}

@app.put("/tickets/{ticket_id}")
def update_ticket(ticket_id: int, payload: dict = Body(...)):
    ticket = next((t for t in tickets.values() if t.get("id") == ticket_id), None)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")

    estado  = (payload.get("estado") or "").strip()
    user_id = (payload.get("user_id") or "").strip()

    if not estado or not user_id:
        raise HTTPException(status_code=422, detail="Faltan campos")

    ticket["estado"]     = estado
    ticket["updated_at"] = datetime.utcnow().isoformat()

    # Notificar al agente CRM
    notify(user_id, f"🔔 Caso {ticket['numero_caso']} → {estado}")

    # Notificar al cliente que creó el ticket
    cliente_user_id = ticket.get("user_id")
    if cliente_user_id:
        mensajes = {
            "Abierto"    : f"📋 Tu caso {ticket['numero_caso']} ha sido reabierto. Nuestro equipo lo revisará pronto.",
            "En Proceso" : f"⚙️ Buenas noticias, tu caso {ticket['numero_caso']} está siendo atendido por un agente. Pronto tendrás una solución.",
            "Cerrado"    : f"✅ Tu caso {ticket['numero_caso']} ha sido resuelto y cerrado. Si el problema persiste escríbenos de nuevo.",
        }
        texto = mensajes.get(estado, f"🔔 Tu caso {ticket['numero_caso']} cambió a: {estado}")
        notify(cliente_user_id, texto)

    return ticket



@app.delete("/tickets/{ticket_id}")
def delete_ticket(ticket_id: int):
    numero_caso = next(
        (k for k, t in tickets.items() if t.get("id") == ticket_id), None
    )
    if not numero_caso:
        raise HTTPException(status_code=404, detail="Ticket no encontrado")
    
    del tickets[numero_caso]
    return {"ok": True, "deleted_id": ticket_id}
 
@app.get("/")
def root():
    return {"status": "ok"}
 