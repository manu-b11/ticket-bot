from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional

# Datos necesarios para crear un ticket
class TicketCreate(BaseModel):
    titulo: str = Field(..., min_length=3, max_length=100)
    cliente: str = Field(..., min_length=2)
    tipo: str
    prioridad: str
    descripcion: str = Field(..., min_length=5)
    contacto: str = Field(..., min_length=7, max_length=15)
    nombre: str = Field(..., min_length=2)
    cargo: str = Field(..., min_length=2)
    numero_contacto: Optional[str] = None
    caso_previo: Optional[str] = None

# Respuesta que devuelve la API
class TicketResponse(BaseModel):
    id: int
    numero_caso: str
    titulo: str
    cliente: str
    tipo: str
    prioridad: str
    descripcion: str
    contacto: str
    nombre: str
    cargo: str
    numero_contacto: Optional[str] = None
    caso_previo: Optional[str] = None
    estado: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# Chat message schema
class ChatMessage(BaseModel):
    user_id: str
    message: str

# Status update schema
class StatusUpdate(BaseModel):
    estado: str
    user_id: str