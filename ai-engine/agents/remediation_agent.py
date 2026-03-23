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


def remediation_agent(state: dict):
    print("[AGENT] Remediation Agent")

    try:
        if state.get("analysis_only"):
            state["auto_policy_decision"] = {
                "run": False,
                "mode": "analysis-only",
                "execute_real": False,
                "reason": "analysis-only-request",
                "action": (state.get("result") or {}).get("recommendation", "investigate"),
            }
            state["remediation_response"] = {
                "status": "skipped",
                "reason": "analysis-only-request",
            }
            state["remediation_attempt"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "agent-remediation",
                "action": (state.get("result") or {}).get("recommendation", "investigate"),
                "mode": "analysis-only",
                "reason": "analysis-only-request",
                "outcome": "skipped",
                "response": state["remediation_response"],
            }
            _trace(state, agent="remediation", status="ok", detail="analysis-only")
            return state

        evaluate_auto_policy = state.get("evaluate_auto_policy_fn")
        execute_remediation = state.get("execute_remediation_fn")
        result = state.get("result") or {}

        if not callable(evaluate_auto_policy) or not callable(execute_remediation):
            state["auto_policy_decision"] = {
                "run": False,
                "mode": "off",
                "execute_real": False,
                "reason": "missing-remediation-callback",
                "action": result.get("recommendation", "investigate"),
            }
            state["remediation_response"] = {
                "status": "skipped",
                "reason": "missing-remediation-callback",
            }
            state["remediation_attempt"] = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "agent-remediation",
                "action": result.get("recommendation", "investigate"),
                "mode": "off",
                "reason": "missing-remediation-callback",
                "outcome": "skipped",
                "response": state["remediation_response"],
            }
            _trace(state, agent="remediation", status="ok", detail="callback-missing-skip")
            return state

        decision = evaluate_auto_policy(
            alert_name=state.get("alert_name", "unknown"),
            pod=result.get("pod") or state.get("pod", "unknown"),
            namespace=state.get("namespace", "default"),
            recommendation=result.get("recommendation"),
            confidence=result.get("confidence", 0),
        )
        state["auto_policy_decision"] = decision

        if decision.get("run"):
            remediation_response = execute_remediation(
                action=result.get("recommendation"),
                pod=result.get("pod") or state.get("pod"),
                namespace=state.get("namespace", "default"),
                deployment=result.get("deployment"),
                replicas=result.get("target_replicas"),
                dry_run=not bool(decision.get("execute_real", False)),
                target_revision=result.get("target_revision"),
                alert_name=state.get("alert_name"),
            )
            outcome = remediation_response.get("status", "unknown")
        else:
            remediation_response = decision
            outcome = "skipped"

        state["remediation_response"] = remediation_response
        state["remediation_attempt"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "agent-remediation",
            "action": result.get("recommendation"),
            "mode": decision.get("mode", "off"),
            "reason": decision.get("reason", "unknown"),
            "outcome": outcome,
            "response": remediation_response,
        }

        _trace(
            state,
            agent="remediation",
            status="ok",
            detail=f"mode={decision.get('mode')} outcome={outcome}",
        )
        return state

    except Exception as error:
        state["agent_error"] = {
            "agent": "remediation",
            "message": str(error),
        }
        state["auto_policy_decision"] = {
            "run": False,
            "mode": "off",
            "execute_real": False,
            "reason": "remediation-agent-error",
            "action": (state.get("result") or {}).get("recommendation", "investigate"),
        }
        state["remediation_response"] = {
            "status": "failed",
            "error": str(error),
        }
        state["remediation_attempt"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "agent-remediation",
            "action": (state.get("result") or {}).get("recommendation", "investigate"),
            "mode": "off",
            "reason": "remediation-agent-error",
            "outcome": "failed",
            "response": state["remediation_response"],
        }
        _trace(state, agent="remediation", status="error", detail=str(error))
        return state
