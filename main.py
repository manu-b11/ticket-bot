

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # ← agregar esto
from pydantic import BaseModel

app = FastAPI()

# ← agregar este bloque
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # en producción pon tu dominio específico
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    user_id: str
    message: str

# -------- BOT CONVERSACIONAL --------
sessions = {}

def process_message(message: str, user_id: str) -> str:
    if user_id not in sessions:
        sessions[user_id] = {"step": 0, "ticket": {}}

    state = sessions[user_id]
    ticket = state["ticket"]

    if state["step"] == 0:
        state["step"] = 1
        return "Hola 👋 ¿Cuál es el problema o incidencia?"

    elif state["step"] == 1:
        ticket["descripcion"] = message
        state["step"] = 2
        return "¿Cuál es la prioridad? (Alta, Media, Baja)"

    elif state["step"] == 2:
        ticket["prioridad"] = message
        state["step"] = 3
        return "¿Qué tipo de incidencia es? (Red, Software, Hardware)"

    elif state["step"] == 3:
        ticket["tipo"] = message
        state["step"] = 4
        return "¿A qué empresa pertenece?"

    elif state["step"] == 4:
        ticket["empresa"] = message
        state["step"] = 5
        return "Nombre de la persona de contacto"

    elif state["step"] == 5:
        ticket["contacto"] = message
        state["step"] = 6
        return "Correo de contacto"

    elif state["step"] == 6:
        ticket["correo"] = message
        state["step"] = 7
        return "Teléfono de contacto"

    elif state["step"] == 7:
        ticket["telefono"] = message
        state["step"] = 8
        return "Describe evidencia o detalles adicionales"

    elif state["step"] == 8:
        ticket["evidencia"] = message
        print("🎟️ Ticket generado:", ticket)
        sessions.pop(user_id)
        return "✅ Ticket listo. (En el MVP, el portal se usa manualmente para finalizar)."

    return "No entendí el mensaje"

@app.post("/chat")
def chat(data: ChatMessage):
    response = process_message(data.message, data.user_id)
    return {"response": response}

# (Opcional) Para evitar 404 en /
@app.get("/")
def root():
    return {"status": "ok"}