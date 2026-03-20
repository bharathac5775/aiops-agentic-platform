from abc import ABC, abstractmethod


class IncidentMemoryStore(ABC):

    @abstractmethod
    def store_incident(self, incident: dict) -> None:
        """Persist incident data for later similarity search."""

    @abstractmethod
    def search_similar(self, query: str, limit: int = 3) -> list[dict]:
        """Return similar incidents ordered by relevance."""
