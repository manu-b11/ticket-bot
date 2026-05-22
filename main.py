from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
 
from ollama_client import analizar_ticket
from rag_engine import cargar_kb, buscar_respuesta
 
app = FastAPI()
 
# ✅ CORS para frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
# ✅ Cargar base RAG al iniciar (solo una vez, después del app)
cargar_kb()
 
class ChatMessage(BaseModel):
    user_id: str
    message: str
 
# -------- BOT CONVERSACIONAL --------
sessions = {}
 
def process_message(message, user_id):
 
    # ✅ RAG: detectar preguntas tipo FAQ
    if "?" in message:
        return buscar_respuesta(message)
 
    # ✅ Flujo normal del bot
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}}
 
    state = sessions[user_id]
    ticket = state["ticket"]
 
    # -------- STEP 0 --------
    if state["step"] == 0:
        state["step"] = 1
        return "Hola 👋 Describe el problema"
 
    # -------- STEP 1 --------
    elif state["step"] == 1:
 
        ticket["descripcion"] = message
 
        # ✅ IA analiza el problema
        resultado = analizar_ticket(message)
 
        ticket["prioridad"] = resultado.get("prioridad", "Media")
        ticket["tipo"] = resultado.get("tipo", "Incidente")
 
        state["step"] = 2
 
        return f"""
✅ Detectamos:
 
⚡ Prioridad: {ticket['prioridad']}
📌 Tipo: {ticket['tipo']}
 
¿Confirmas? (sí/no)
"""
 
    # -------- STEP 2 --------
    elif state["step"] == 2:
 
        if message.lower() == "sí":
            sessions.pop(user_id)
            return "✅ Ticket creado correctamente"
        else:
            sessions.pop(user_id)
            return "❌ Ticket cancelado"
 
    return "No entendí el mensaje"
 
# -------- ENDPOINT --------
@app.post("/chat")
def chat(data: ChatMessage):
    response = process_message(data.message, data.user_id)
    return {"response": response}
 
@app.get("/")
def root():
    return {"status": "ok"}