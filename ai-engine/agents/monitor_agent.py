from datetime import datetime, timezone

from tools.loki_client import get_pod_logs
from tools.prometheus_client import (
    get_pod_cpu_usage,
    get_pod_memory_usage,
    get_pod_oomkilled_status,
    get_pod_restart_count,
)


def _trace(state: dict, agent: str, status: str, detail: str = ""):
    trace = state.get("agent_trace", [])
    trace.append(
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "status": status,
            "detail": detail,
        }
    )
    state["agent_trace"] = trace


def monitor_agent(state: dict):
    print("[AGENT] Monitor Agent")

    try:
        alert = state.get("alert", {})
        labels = alert.get("labels") or {}

        alert_name = labels.get("alertname", "unknown")
        pod = labels.get("pod", "unknown")
        namespace = labels.get("namespace", "default")

        state["alert_name"] = alert_name
        state["pod"] = pod
        state["namespace"] = namespace

        cpu_usage = get_pod_cpu_usage(pod)
        memory_usage = get_pod_memory_usage(pod)
        restart_count_5m = get_pod_restart_count(pod)
        oomkilled = get_pod_oomkilled_status(pod)

        state["metrics"] = {
            "cpu_usage": cpu_usage,
            "memory_usage_bytes": memory_usage,
            "restart_count_5m": restart_count_5m,
            "oomkilled": oomkilled,
        }

        try:
            logs = get_pod_logs(pod)
            state["logs"] = logs
        except Exception as log_error:
            print(f"[Monitor WARN] log fetch failed: {log_error}")
            state["logs"] = []

        _trace(
            state,
            agent="monitor",
            status="ok",
            detail=f"alert={alert_name} pod={pod} namespace={namespace}",
        )
        return state

    except Exception as error:
        state["metrics"] = {
            "cpu_usage": 0,
            "memory_usage_bytes": 0,
            "restart_count_5m": 0,
            "oomkilled": 0,
        }
        state["logs"] = []
        state["agent_error"] = {
            "agent": "monitor",
            "message": str(error),
        }
        _trace(state, agent="monitor", status="error", detail=str(error))
        return state
