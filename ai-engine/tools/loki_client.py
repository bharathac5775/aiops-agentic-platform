import requests
from datetime import datetime, timezone

LOKI_URL = "http://loki.monitoring.svc.cluster.local:3100"


def query_loki(query: str, limit: int = 20, lookback_minutes: int = 5):
    url = f"{LOKI_URL}/loki/api/v1/query_range"

    end_ns = int(datetime.now(timezone.utc).timestamp() * 1_000_000_000)
    start_ns = end_ns - (lookback_minutes * 60 * 1_000_000_000)

    response = requests.get(
        url,
        params={
            "query": query,
            "start": start_ns,
            "end": end_ns,
            "limit": limit,
            "direction": "backward",
        },
        timeout=10,
    )

    if response.status_code != 200:
        raise Exception(f"Loki query failed: {response.text}")

    data = response.json()

    return data.get("data", {}).get("result", [])


def _extract_logs(result):
    logs = []

    for stream in result:
        for value in stream.get("values", []):
            logs.append(value[1])

    return logs


def get_pod_logs(pod_name: str, limit: int = 20):
    if not pod_name:
        return []

    # Prefer the common Promtail pod label and fall back to kubernetes_pod_name label.
    selectors = [
        f'{{pod="{pod_name}"}}',
        f'{{kubernetes_pod_name="{pod_name}"}}',
    ]

    logs = []

    for query in selectors:
        result = query_loki(query, limit=limit)
        logs = _extract_logs(result)
        if logs:
            break

    return logs[-limit:]
