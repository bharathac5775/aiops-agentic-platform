from langgraph.graph import StateGraph

from agents.monitor_agent import monitor_agent
from agents.rca_agent import rca_agent
from agents.remediation_agent import remediation_agent
from agents.report_agent import report_agent


def fallback_agent(state: dict):
    print("[AGENT] Fallback Agent")
    state["result"] = state.get("result") or {
        "alert_name": state.get("alert_name", "unknown"),
        "pod": state.get("pod", "unknown"),
        "root_cause": "Agent chain fallback triggered",
        "recommendation": "investigate",
        "confidence": 0.4,
        "decision_source": "fallback",
        "recommended_by": "fallback",
        "guardrail_notes": ["agent-fallback"],
    }
    return state


def _route_on_error(state: dict):
    return "fallback" if state.get("agent_error") else "ok"


def build_agent_graph():
    graph = StateGraph(dict)

    graph.add_node("monitor", monitor_agent)
    graph.add_node("rca", rca_agent)
    graph.add_node("remediate", remediation_agent)
    graph.add_node("fallback", fallback_agent)
    graph.add_node("report", report_agent)

    graph.set_entry_point("monitor")

    graph.add_conditional_edges(
        "monitor",
        _route_on_error,
        {
            "ok": "rca",
            "fallback": "fallback",
        },
    )

    graph.add_conditional_edges(
        "rca",
        _route_on_error,
        {
            "ok": "remediate",
            "fallback": "fallback",
        },
    )

    graph.add_conditional_edges(
        "remediate",
        _route_on_error,
        {
            "ok": "report",
            "fallback": "fallback",
        },
    )

    graph.add_edge("fallback", "report")

    return graph.compile()
