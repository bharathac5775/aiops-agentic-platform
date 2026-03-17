from langgraph.graph import StateGraph
from tools.prometheus_client import get_pod_cpu_usage


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

    print(f"Fetching CPU metrics for pod: {pod}")

    try:
        cpu_usage = get_pod_cpu_usage(pod)

        state["metrics"] = {
            "cpu_usage": cpu_usage
        }

        print(f"CPU Usage: {cpu_usage}")

    except Exception as e:
        print(f"Prometheus error: {e}")
        state["metrics"] = {
            "cpu_usage": 0
        }

    return state


def decide_action(state):
    print("Deciding remediation action")

    cpu = state.get("metrics", {}).get("cpu_usage", 0)
    pod = state.get("pod", "unknown")

    print(f"[METRICS] Pod={pod} CPU={cpu}")

    if cpu == 0:
        print("[WARNING] CPU metric missing or zero")

        decision = "investigate"
        root_cause = "Metrics unavailable or pod idle"
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

    state["decision"] = decision

    state["result"] = {
        "root_cause": root_cause,
        "recommendation": decision,
        "confidence": confidence
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