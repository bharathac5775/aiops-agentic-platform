import requests

PROMETHEUS_URL = "http://monitoring-kube-prometheus-prometheus.monitoring:9090"


def query_prometheus(query: str):
    url = f"{PROMETHEUS_URL}/api/v1/query"

    response = requests.get(url, params={"query": query}, timeout=10)

    if response.status_code != 200:
        raise Exception(f"Prometheus query failed: {response.text}")

    data = response.json()

    return data["data"]["result"]


def _first_value_or_zero(result):
    if not result:
        return 0
    return float(result[0]["value"][1])


def get_pod_cpu_usage(pod_name: str):
    if not pod_name:
        return 0

    query = f'sum(rate(container_cpu_usage_seconds_total{{pod="{pod_name}"}}[2m]))'

    result = query_prometheus(query)

    if not result:
        # Fallback for clusters where namespace label is required in series matching.
        query = f'sum(rate(container_cpu_usage_seconds_total{{namespace="default",pod="{pod_name}"}}[2m]))'
        result = query_prometheus(query)

    if not result:
        return 0

    return float(result[0]["value"][1])


def get_pod_memory_usage(pod_name: str):
    if not pod_name:
        return 0

    query = f'sum(container_memory_working_set_bytes{{namespace="default",pod="{pod_name}"}})'
    result = query_prometheus(query)

    return _first_value_or_zero(result)


def get_pod_restart_count(pod_name: str):
    if not pod_name:
        return 0

    query = f'sum(increase(kube_pod_container_status_restarts_total{{namespace="default",pod="{pod_name}"}}[5m]))'
    result = query_prometheus(query)

    return _first_value_or_zero(result)


def get_pod_oomkilled_status(pod_name: str):
    if not pod_name:
        return 0

    query = f'max(kube_pod_container_status_last_terminated_reason{{namespace="default",pod="{pod_name}",reason="OOMKilled"}})'
    result = query_prometheus(query)

    return _first_value_or_zero(result)
