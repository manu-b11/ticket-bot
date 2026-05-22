from sqlalchemy import Column, Integer, String
from database import Base


# Modelo de la tabla tickets
class Ticket(Base):

    __tablename__ = "tickets"

    id = Column(Integer, primary_key=True, index=True)

    numero_caso = Column(String, unique=True)

    titulo = Column(String, nullable=False)

    cliente = Column(String, nullable=False)

    tipo = Column(String, nullable=False)

    prioridad = Column(String, nullable=False)

    descripcion = Column(String, nullable=False)

    contacto = Column(String, nullable=False)

    nombre = Column(String, nullable=False)

    cargo = Column(String, nullable=False)

    estado = Column(String, default="Abierto")