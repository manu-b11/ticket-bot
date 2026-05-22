import requests
import chromadb
 
# ✅ Clase corregida con método name()
class OllamaEmbeddingFunction:
 
    def name(self):
        return "ollama-all-minilm"
 
    def __call__(self, input):
        response = requests.post(
            "http://localhost:11434/api/embed",
            json={
                "model": "all-minilm",
                "input": input
            }
        )
        return response.json()["embeddings"]
 
 
# ✅ EphemeralClient (en memoria, compatible con todas las versiones)
client = chromadb.EphemeralClient()
 
collection = client.get_or_create_collection(
    name="kb_docs",   # 7 caracteres → válido
    embedding_function=OllamaEmbeddingFunction()
)
 
 
# ✅ Cargar base de conocimiento
def cargar_kb():
 
    documentos = [
        "Alta prioridad es cuando el sistema está caído o sin servicio",
        "Media prioridad es cuando hay fallas parciales",
        "Baja prioridad es para solicitudes o consultas",
        "NV1 es el primer nivel de soporte",
        "Los tickets pueden escalarse a otros niveles"
    ]
 
    ids = [str(i) for i in range(len(documentos))]
 
    # ✅ upsert evita error si los IDs ya existen
    collection.upsert(
        documents=documentos,
        ids=ids
    )
 
 
# ✅ Buscar respuesta
def buscar_respuesta(pregunta):
 
    resultados = collection.query(
        query_texts=[pregunta],
        n_results=1
    )
 
    if resultados["documents"]:
        return resultados["documents"][0][0]
 
    return "No tengo información sobre eso"