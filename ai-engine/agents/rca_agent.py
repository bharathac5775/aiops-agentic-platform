from datetime import datetime, timezone

from workflows.cpu_workflow import decide_action, pre_decision_check, rca_analysis, route_after_metrics


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


def rca_agent(state: dict):
    print("[AGENT] RCA Agent")

    try:
        pre_decision_check(state)

        if state.get("skip_llm"):
            decide_action(state)
            _trace(state, agent="rca", status="ok", detail="precheck-fast-path")
            return state

        next_stage = route_after_metrics(state)
        if next_stage == "collect_logs":
            # Monitor agent already attempted log collection; do not fail if logs are absent.
            if not isinstance(state.get("logs"), list):
                state["logs"] = []

        rca_analysis(state)
        decide_action(state)

        source = (state.get("result") or {}).get("decision_source", "unknown")
        _trace(state, agent="rca", status="ok", detail=f"decision_source={source}")
        return state

    except Exception as error:
        state["agent_error"] = {
            "agent": "rca",
            "message": str(error),
        }

        # Preserve fallback behavior when RCA processing fails.
        state["result"] = state.get("result") or {
            "alert_name": state.get("alert_name", "unknown"),
            "pod": state.get("pod", "unknown"),
            "root_cause": "RCA agent failure; using safe fallback",
            "recommendation": "investigate",
            "confidence": 0.4,
            "decision_source": "agent-fallback",
            "recommended_by": "fallback",
            "guardrail_notes": ["rca-agent-error"],
            "reasoning_trace": {
                "used_metrics": bool(state.get("metrics")),
                "used_logs": bool(state.get("logs")),
                "llm_used": False,
                "guardrails_applied": ["agent-fallback"],
            },
            "similar_incidents": state.get("similar_incidents", []),
        }

        _trace(state, agent="rca", status="error", detail=str(error))
        return state
