from langgraph.graph import StateGraph


def analyze_alert(state):

    alert = state["alert"]

    alertname = alert["labels"].get("alertname")
    pod = alert["labels"].get("pod")

    print(f"Analyzing alert: {alertname} on pod {pod}")

    return state


def collect_metrics(state):
    print("Collecting metrics from Prometheus")
    return state


def decide_action(state):
    print("Deciding remediation action")
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