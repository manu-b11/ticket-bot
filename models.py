from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from datetime import datetime
from database import Base
 
 
class Ticket(Base):
    __tablename__ = "tickets"
 
    id              = Column(Integer, primary_key=True, index=True)
    numero_caso     = Column(String, unique=True, index=True, nullable=False)
    titulo          = Column(String, nullable=False)
    cliente         = Column(String, nullable=False)
    tipo            = Column(String, nullable=False)
    prioridad       = Column(String, nullable=False)
    descripcion     = Column(Text, nullable=False)
    contacto        = Column(String, nullable=False)
    nombre          = Column(String, nullable=False)
    cargo           = Column(String, nullable=False)
    numero_contacto = Column(String, nullable=True)
    caso_previo     = Column(String, nullable=True)
    marca           = Column(String, nullable=True)
    estado          = Column(String, default="Abierto")
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
 
 
class SolucionKB(Base):
    """Almacena soluciones generadas para errores repetitivos."""
    __tablename__ = "soluciones_kb"
 
    id          = Column(Integer, primary_key=True, index=True)
    marca       = Column(String, nullable=False, index=True)
    error_desc  = Column(Text, nullable=False)
    solucion    = Column(Text, nullable=False)
    numero_caso = Column(String, nullable=True)
    creado_en   = Column(DateTime, server_default=func.now())
    veces_usado = Column(Integer, default=0)