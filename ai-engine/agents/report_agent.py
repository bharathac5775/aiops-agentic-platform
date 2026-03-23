import uuid
from datetime import datetime, timezone


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


def report_agent(state: dict):
    print("[AGENT] Report Agent")

    incident_report = {
        "incident_id": f"inc-{uuid.uuid4().hex[:12]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "alert_name": state.get("alert_name", "unknown"),
        "pod": state.get("pod", "unknown"),
        "namespace": state.get("namespace", "default"),
        "analysis": state.get("result", {}),
        "decision": state.get("auto_policy_decision", {}),
        "remediation": state.get("remediation_response", {}),
        "similar_incidents": state.get("similar_incidents", []),
        "agent_trace": state.get("agent_trace", []),
        "agent_error": state.get("agent_error"),
    }
    state["incident_report"] = incident_report
    _trace(state, agent="report", status="ok", detail="incident-report-prepared")
    return state
