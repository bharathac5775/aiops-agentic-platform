from langgraph.graph import StateGraph


def analyze_alert(state):

    alert = state["alert"]

    alertname = alert["labels"].get("alertname")
    pod = alert["labels"].get("pod")

    print(f"Analyzing alert: {alertname} on pod {pod}")

    state["alert_name"] = alertname
    state["pod"] = pod

    return state


def collect_metrics(state):
    print("Collecting metrics from Prometheus")

    # Placeholder (Day 9 will replace this)
    state["metrics"] = {
        "cpu_usage": "85%",
        "memory_usage": "60%"
    }

    return state


def decide_action(state):
    print("Deciding remediation action")

    alertname = state.get("alert_name")

    # Simple rule-based decision (Day 12 will replace with LLM)
    if alertname == "HighPodCPUUsage":
        decision = "scale deployment"
    else:
        decision = "no action"

    state["decision"] = decision

    # Final structured result
    state["result"] = {
        "root_cause": "High CPU usage",
        "recommendation": decision,
        "confidence": 0.85
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