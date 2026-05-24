from sqlalchemy import Column, Integer, String, Text, DateTime
from datetime import datetime
from database import Base

# Modelo de la tabla tickets
class Ticket(Base):
    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)
    numero_caso = Column(String, unique=True, index=True, nullable=False)
    titulo = Column(String, nullable=False)
    cliente = Column(String, nullable=False)
    tipo = Column(String, nullable=False)
    prioridad = Column(String, nullable=False)
    descripcion = Column(Text, nullable=False)
    contacto = Column(String, nullable=False)
    nombre = Column(String, nullable=False)
    cargo = Column(String, nullable=False)
    numero_contacto = Column(String, nullable=True)
    caso_previo = Column(String, nullable=True)
    estado = Column(String, default="Abierto")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)