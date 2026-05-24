import os
import glob
import re
from sqlalchemy.orm import Session
 
# ── Marcas soportadas ──────────────────────────────────────────────────────────
MARCAS_SOPORTADAS = {"cisco", "fortinet", "nozomi", "ekahau"}
KB_FOLDER = "kb"
 
# ── Índice en memoria (marca -> lista de chunks) ───────────────────────────────
_indice: dict[str, list[dict]] = {}   # {"cisco": [{"texto": ..., "fuente": ...}]}
 
 
def cargar_kb():
    """Lee todos los .txt de kb/{marca}/ y los carga en _indice."""
    global _indice
    _indice = {}
    total = 0
 
    for marca in MARCAS_SOPORTADAS:
        patron   = os.path.join(KB_FOLDER, marca, "*.txt")
        archivos = glob.glob(patron)
        chunks   = []
 
        for fp in archivos:
            with open(fp, "r", encoding="utf-8") as f:
                contenido = f.read()
            for chunk in _chunk(contenido):
                chunks.append({"texto": chunk, "fuente": os.path.basename(fp)})
                total += 1
 
        _indice[marca] = chunks
 
    if total == 0:
        print("⚠️  KB vacía: agrega .txt en kb/cisco/, kb/fortinet/, etc.")
    else:
        print(f"✅ KB cargada: {total} chunks ({', '.join(MARCAS_SOPORTADAS)})")
 
 
def _chunk(texto: str, size: int = 800, overlap: int = 100) -> list:
    texto  = texto.replace("\r", "")
    chunks, i = [], 0
    while i < len(texto):
        c = texto[i: i + size].strip()
        if c:
            chunks.append(c)
        i += size - overlap
    return chunks
 
 
def _score(query: str, texto: str) -> int:
    """Cuenta cuántas palabras clave del query aparecen en el texto."""
    palabras = set(re.findall(r"\w{3,}", query.lower()))
    texto_l  = texto.lower()
    return sum(1 for p in palabras if p in texto_l)
 
 
# ── Búsqueda por palabras clave ────────────────────────────────────────────────
def buscar_respuesta(pregunta: str, marca: str = None) -> str:
    """
    Busca el chunk más relevante.
    Si se pasa 'marca', busca solo en esa marca; si no, busca en todas.
    """
    marcas_buscar = [marca.lower()] if marca and marca.lower() in _indice else list(_indice.keys())
 
    mejor_score  = 0
    mejor_chunk  = None
    mejor_fuente = ""
    mejor_marca  = ""
 
    for m in marcas_buscar:
        for item in _indice.get(m, []):
            s = _score(pregunta, item["texto"])
            if s > mejor_score:
                mejor_score  = s
                mejor_chunk  = item["texto"]
                mejor_fuente = item["fuente"]
                mejor_marca  = m
 
    if not mejor_chunk or mejor_score < 2:
        return _sin_info()
 
    return f"{mejor_chunk}\n\n📎 Fuente: {mejor_marca.capitalize()} — {mejor_fuente}"
 
 
def _sin_info() -> str:
    return (
        "No encontré información específica en la documentación oficial. "
        "Puedo crear un ticket o escalarlo a NV1 para revisión."
    )
 
 
# ── Guardar solución aprendida ─────────────────────────────────────────────────
def guardar_solucion(db: Session, marca: str, error_desc: str,
                     solucion: str, numero_caso: str = None):
    from models import SolucionKB
 
    nueva = SolucionKB(
        marca=marca.lower(),
        error_desc=error_desc,
        solucion=solucion,
        numero_caso=numero_caso,
    )
    db.add(nueva)
    db.commit()
    db.refresh(nueva)
    print(f"💾 Solución guardada para '{marca}' (caso {numero_caso})")
    return nueva
 
 
def buscar_solucion_previa(db: Session, marca: str, descripcion: str) -> str | None:
    from models import SolucionKB
 
    soluciones = (
        db.query(SolucionKB)
        .filter(SolucionKB.marca == marca.lower())
        .order_by(SolucionKB.veces_usado.desc())
        .limit(20)
        .all()
    )
 
    desc_lower = descripcion.lower()
    for sol in soluciones:
        palabras = [p for p in sol.error_desc.lower().split() if len(p) > 4]
        hits = sum(1 for p in palabras if p in desc_lower)
        if hits >= 2:
            sol.veces_usado += 1
            db.commit()
            return (
                f"✅ Solución de caso previo ({sol.numero_caso or 'interno'}):\n"
                f"{sol.solucion}"
            )
    return None