from langgraph.graph import StateGraph
from tools.prometheus_client import (
    get_pod_cpu_usage,
    get_pod_memory_usage,
    get_pod_restart_count,
    get_pod_oomkilled_status,
)


def analyze_alert(state):

    alert = state["alert"]

    alertname = alert["labels"].get("alertname")
    pod = alert["labels"].get("pod")

    if not pod:
        print("No pod found in alert")

    print(f"Analyzing alert: {alertname} on pod {pod}")

    state["alert_name"] = alertname
    state["pod"] = pod

    return state


def collect_metrics(state):
    pod = state.get("pod")

    print(f"Fetching Prometheus metrics for pod: {pod}")

    try:
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

        print(
            "[METRICS] "
            f"Pod={pod} "
            f"CPU={cpu_usage} "
            f"MEM={memory_usage} "
            f"RESTARTS_5M={restart_count_5m} "
            f"OOMKILLED={oomkilled}"
        )

    except Exception as e:
        print(f"Prometheus error: {e}")
        state["metrics"] = {
            "cpu_usage": 0,
            "memory_usage_bytes": 0,
            "restart_count_5m": 0,
            "oomkilled": 0,
        }

    return state


def decide_action(state):
    print("Deciding remediation action")

    alert_name = state.get("alert_name", "unknown")
    cpu = state.get("metrics", {}).get("cpu_usage", 0)
    memory = state.get("metrics", {}).get("memory_usage_bytes", 0)
    restarts = state.get("metrics", {}).get("restart_count_5m", 0)
    oomkilled = state.get("metrics", {}).get("oomkilled", 0)
    pod = state.get("pod", "unknown")

    print(
        "[DECISION_INPUT] "
        f"alert={alert_name} "
        f"pod={pod} "
        f"cpu={cpu} "
        f"memory_bytes={memory} "
        f"restarts_5m={restarts} "
        f"oomkilled={oomkilled}"
    )

    if alert_name == "HighPodCPUUsage":
        if cpu == 0:
            print("[WARNING] CPU metric missing or zero")
            decision = "investigate"
            root_cause = "CPU metrics unavailable or pod idle"
            confidence = 0.5
        elif cpu > 0.85:
            decision = "scale deployment"
            root_cause = "High CPU saturation"
            confidence = 0.95
        elif cpu > 0.7:
            decision = "monitor"
            root_cause = "Moderate CPU usage"
            confidence = 0.85
        else:
            decision = "no action"
            root_cause = "CPU normal"
            confidence = 0.8

    elif alert_name == "HighMemoryUsage":
        if memory <= 0:
            decision = "investigate"
            root_cause = "Memory metrics unavailable"
            confidence = 0.5
        elif memory > 500_000_000:
            decision = "restart pod"
            root_cause = "High memory working set detected"
            confidence = 0.9
        else:
            decision = "monitor"
            root_cause = "Memory usage not above threshold now"
            confidence = 0.75

    elif alert_name == "PodCrashLoop":
        if restarts > 3:
            decision = "investigate and restart pod"
            root_cause = "Frequent container restarts (CrashLoop pattern)"
            confidence = 0.95
        else:
            decision = "monitor"
            root_cause = "Restart rate currently below critical threshold"
            confidence = 0.7

    elif alert_name == "PodOOMKilled":
        if oomkilled >= 1:
            decision = "increase memory limit and restart pod"
            root_cause = "Container was OOMKilled"
            confidence = 0.95
        else:
            decision = "investigate"
            root_cause = "OOMKilled alert fired but metric not present now"
            confidence = 0.65

    else:
        decision = "investigate"
        root_cause = f"Unhandled alert type: {alert_name}"
        confidence = 0.6

    state["decision"] = decision

    state["result"] = {
        "alert_name": alert_name,
        "pod": pod,
        "root_cause": root_cause,
        "recommendation": decision,
        "confidence": confidence,
        "observed_metrics": {
            "cpu_usage": cpu,
            "memory_usage_bytes": memory,
            "restart_count_5m": restarts,
            "oomkilled": oomkilled,
        },
    }

    return state


def build_graph():

    graph = StateGraph(dict)

    graph.add_node("analyze_alert", analyze_alert)
    graph.add_node("collect_metrics", collect_metrics)
    graph.add_node("decide_action", decide_action)

    graph.set_entry_point("analyze_alert")

    graph.add_edge("analyze_alert", "collect_metrics")
    graph.add_edge("collect_metrics", "decide_action")

    return graph.compile()