import os

from tools.rag.base import IncidentMemoryStore
from tools.rag.chroma_store import ChromaIncidentMemoryStore


def _resolve_backend_name() -> str:
    return str(os.getenv("INCIDENT_MEMORY_BACKEND", "chroma") or "chroma").strip().lower()


def _resolve_persist_dir() -> str:
    default_path = "/data/incidents/chroma"
    return str(os.getenv("INCIDENT_MEMORY_PATH", default_path) or default_path).strip()


class _NoopMemoryStore(IncidentMemoryStore):

    def store_incident(self, incident: dict) -> None:
        return None

    def search_similar(self, query: str, limit: int = 3) -> list[dict]:
        return []


def create_incident_memory_store() -> IncidentMemoryStore:
    backend = _resolve_backend_name()

    if backend == "chroma":
        try:
            return ChromaIncidentMemoryStore(
                persist_directory=_resolve_persist_dir(),
                collection_name=str(os.getenv("INCIDENT_MEMORY_COLLECTION", "incident_memory") or "incident_memory"),
            )
        except Exception as error:
            print(f"[RAG] Failed to initialize Chroma backend, falling back to noop: {error}")
            return _NoopMemoryStore()

    print(f"[RAG] Unsupported backend '{backend}', falling back to noop")
    return _NoopMemoryStore()


incident_memory_store = create_incident_memory_store()
