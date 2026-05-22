from pydantic import BaseModel, EmailStr, Field


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


# Respuesta que devuelve la API
class TicketResponse(TicketCreate):

    id: int
    estado: str

    class Config:
        from_attributes = True