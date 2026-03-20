import json
import os
from datetime import datetime, timezone

import chromadb

from tools.rag.base import IncidentMemoryStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ChromaIncidentMemoryStore(IncidentMemoryStore):

    def __init__(self, persist_directory: str, collection_name: str = "incident_memory"):
        os.makedirs(persist_directory, exist_ok=True)
        self._client = chromadb.PersistentClient(path=persist_directory)
        self._collection = self._client.get_or_create_collection(name=collection_name)

    def _build_document(self, incident: dict) -> str:
        analysis = incident.get("analysis") or {}
        alert = incident.get("alert") or {}
        labels = alert.get("labels") or {}

        parts = [
            f"incident_id: {incident.get('incident_id', '')}",
            f"alert_name: {incident.get('alert_name', labels.get('alertname', 'unknown'))}",
            f"namespace: {incident.get('namespace', labels.get('namespace', 'default'))}",
            f"pod: {incident.get('pod', labels.get('pod', 'unknown'))}",
            f"root_cause: {analysis.get('root_cause', '')}",
            f"recommendation: {analysis.get('recommendation', '')}",
            f"confidence: {analysis.get('confidence', '')}",
            f"decision_source: {analysis.get('decision_source', '')}",
        ]

        notes = analysis.get("guardrail_notes") or []
        if notes:
            parts.append(f"guardrail_notes: {', '.join(str(n) for n in notes)}")

        metrics = analysis.get("observed_metrics") or {}
        if metrics:
            parts.append(f"observed_metrics: {json.dumps(metrics, ensure_ascii=True)}")

        return "\n".join(parts)

    def _build_metadata(self, incident: dict) -> dict:
        analysis = incident.get("analysis") or {}
        alert = incident.get("alert") or {}
        labels = alert.get("labels") or {}

        return {
            "incident_id": str(incident.get("incident_id") or ""),
            "correlation_id": str(incident.get("correlation_id") or ""),
            "alert_name": str(incident.get("alert_name") or labels.get("alertname") or "unknown"),
            "namespace": str(incident.get("namespace") or labels.get("namespace") or "default"),
            "pod": str(incident.get("pod") or labels.get("pod") or "unknown"),
            "recommendation": str(analysis.get("recommendation") or ""),
            "root_cause": str(analysis.get("root_cause") or ""),
            "created_at": str(incident.get("created_at") or _utc_now_iso()),
            "source": str(incident.get("source") or "unknown"),
        }

    def store_incident(self, incident: dict) -> None:
        if not incident:
            return

        incident_id = str(incident.get("incident_id") or "").strip()
        if not incident_id:
            return

        document = self._build_document(incident)
        metadata = self._build_metadata(incident)

        self._collection.upsert(
            ids=[incident_id],
            documents=[document],
            metadatas=[metadata],
        )

    def search_similar(self, query: str, limit: int = 3) -> list[dict]:
        if not query or limit <= 0:
            return []

        result = self._collection.query(
            query_texts=[query],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        items = []
        for idx, document in enumerate(documents):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            distance = distances[idx] if idx < len(distances) else None
            items.append(
                {
                    "metadata": metadata or {},
                    "document": document or "",
                    "distance": distance,
                }
            )

        return items
