from langgraph.graph import StateGraph
import json
import re
from tools.prometheus_client import (
    get_pod_cpu_usage,
    get_pod_memory_usage,
    get_pod_restart_count,
    get_pod_oomkilled_status,
)
from tools.loki_client import get_pod_logs
from tools.llm_client import call_llm

ALLOWED_RECOMMENDATIONS = {
    "scale deployment",
    "restart pod",
    "monitor",
    "investigate",
    "increase memory limit and restart pod",
    "no action",
    "investigate and restart pod",
}


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


def collect_logs(state):
    pod = state.get("pod")

    print(f"Fetching logs from Loki for pod: {pod}")

    try:
        logs = get_pod_logs(pod)
        state["logs"] = logs
        print(f"[LOGS] Retrieved {len(logs)} log lines")
    except Exception as e:
        print(f"Loki error: {e}")
        state["logs"] = []

    return state


def _extract_llm_json(text: str):
    if not text:
        return {}

    # Try full-text JSON first.
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass

    # Fallback to first JSON object found in the response.
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return {}

    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _safe_float(value, default: float = 0.7):
    try:
        return float(value)
    except Exception:
        return default


def _clamp_confidence(value):
    return max(0.0, min(1.0, _safe_float(value)))


def rca_analysis(state):
    print("Running LLM-based RCA")

    alert_name = state.get("alert_name", "unknown")
    pod = state.get("pod", "unknown")
    metrics = state.get("metrics", {})
    logs = state.get("logs", [])[-10:]

    prompt = f"""
You are an expert Kubernetes Site Reliability Engineer.

Task: perform root cause analysis for one Kubernetes alert using metrics and logs.

Input:
- Alert Name: {alert_name}
- Pod: {pod}
- CPU usage cores: {metrics.get('cpu_usage')}
- Memory usage bytes: {metrics.get('memory_usage_bytes')}
- Restart count (5m): {metrics.get('restart_count_5m')}
- OOMKilled signal: {metrics.get('oomkilled')}
- Recent logs (latest 10): {logs}

Rules:
- Be concise and production-safe.
- If evidence is weak, recommendation should be "investigate".
- Recommendation must be one of:
  ["scale deployment", "restart pod", "monitor", "investigate", "increase memory limit and restart pod", "no action"]
- Confidence must be a float between 0.0 and 1.0.

Hard constraints:
- If alert is HighMemoryUsage and memory_usage_bytes > 500000000 with error logs present, avoid "investigate".
- If alert is HighPodCPUUsage and cpu_usage > 0.85 with timeout logs present, prefer "scale deployment".
- If alert is PodCrashLoop and restart_count_5m > 3, avoid "no action".
- If alert is PodOOMKilled and oomkilled >= 1, prefer "increase memory limit and restart pod".

Respond with ONLY valid JSON, no markdown:
{{
  "root_cause": "...",
  "recommendation": "...",
  "confidence": 0.0
}}
""".strip()

    try:
        response = call_llm(prompt)
        print(f"[LLM RESPONSE] {response}")
        state["llm_output"] = response
        state["llm_json"] = _extract_llm_json(response)
    except Exception as e:
        print(f"LLM error: {e}")
        state["llm_output"] = ""
        state["llm_json"] = {}

    return state


def decide_action(state):
    print("Deciding remediation action")

    alert_name = state.get("alert_name", "unknown")
    cpu = state.get("metrics", {}).get("cpu_usage", 0)
    memory = state.get("metrics", {}).get("memory_usage_bytes", 0)
    restarts = state.get("metrics", {}).get("restart_count_5m", 0)
    oomkilled = state.get("metrics", {}).get("oomkilled", 0)
    pod = state.get("pod", "unknown")
    logs = state.get("logs", [])
    llm_json = state.get("llm_json", {})

    print(f"[LOG_ANALYSIS] Sample logs: {logs[-3:]}")

    error_keywords = ["error", "exception", "fail", "traceback"]
    error_logs = [
        log for log in logs
        if any(keyword in log.lower() for keyword in error_keywords)
    ]
    timeout_logs = [log for log in logs if "timeout" in log.lower()]
    strong_memory_signal = memory > 500_000_000 and len(error_logs) > 0
    strong_cpu_timeout_signal = cpu > 0.85 and len(timeout_logs) > 0
    strong_crash_signal = restarts > 3
    strong_oom_signal = oomkilled >= 1

    if llm_json:
        recommendation = str(llm_json.get("recommendation", "investigate")).strip().lower()
        root_cause = str(llm_json.get("root_cause", "LLM analysis")).strip()
        confidence = _clamp_confidence(llm_json.get("confidence", 0.7))
        guardrail_notes = []

        if recommendation not in ALLOWED_RECOMMENDATIONS:
            recommendation = "investigate"
            guardrail_notes.append("invalid recommendation normalized")

        if alert_name == "HighMemoryUsage" and strong_memory_signal and recommendation in {"investigate", "monitor", "no action"}:
            recommendation = "restart pod"
            root_cause = "High memory usage with recurring application errors"
            confidence = max(confidence, 0.88)
            guardrail_notes.append("memory+error guardrail")

        if alert_name == "HighPodCPUUsage" and strong_cpu_timeout_signal and recommendation != "scale deployment":
            recommendation = "scale deployment"
            root_cause = "High CPU with timeout errors in logs"
            confidence = max(confidence, 0.9)
            guardrail_notes.append("cpu+timeout guardrail")

        if alert_name == "PodCrashLoop" and strong_crash_signal and recommendation in {"no action", "monitor", "investigate"}:
            recommendation = "investigate and restart pod"
            root_cause = "CrashLoop pattern detected with elevated restart count"
            confidence = max(confidence, 0.9)
            guardrail_notes.append("crashloop guardrail")

        if alert_name == "PodOOMKilled" and strong_oom_signal and recommendation != "increase memory limit and restart pod":
            recommendation = "increase memory limit and restart pod"
            root_cause = "OOMKilled signal detected in metrics"
            confidence = max(confidence, 0.92)
            guardrail_notes.append("oom guardrail")

        decision_source = "llm-guardrailed" if guardrail_notes else "llm"
        recommended_by = "guardrail" if guardrail_notes else "llm"

        state["decision"] = recommendation
        state["result"] = {
            "alert_name": alert_name,
            "pod": pod,
            "root_cause": root_cause,
            "recommendation": recommendation,
            "confidence": confidence,
            "decision_source": decision_source,
            "recommended_by": recommended_by,
            "guardrail_notes": guardrail_notes,
            "log_error_count": len(error_logs),
            "log_insights": logs[-5:],
            "observed_metrics": {
                "cpu_usage": cpu,
                "memory_usage_bytes": memory,
                "restart_count_5m": restarts,
                "oomkilled": oomkilled,
            },
            "llm_output": state.get("llm_output", ""),
        }
        return state

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
        elif cpu > 0.85 and timeout_logs:
            decision = "scale deployment"
            root_cause = "High CPU + timeout errors in logs"
            confidence = 0.95
        elif cpu > 0.85 and error_logs:
            decision = "restart pod"
            root_cause = "High CPU with application errors in logs"
            confidence = 0.92
        elif cpu > 0.85:
            decision = "monitor"
            root_cause = "High CPU without corroborating error logs"
            confidence = 0.82
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
        elif any("oom" in log.lower() for log in logs) or oomkilled >= 1:
            decision = "increase memory limit and restart pod"
            root_cause = "Memory pressure and OOM indicators in logs/metrics"
            confidence = 0.95
        elif memory > 500_000_000 and error_logs:
            decision = "restart pod"
            root_cause = "High memory with application error logs"
            confidence = 0.9
        elif memory > 500_000_000:
            decision = "restart pod"
            root_cause = "High memory working set detected"
            confidence = 0.9
        else:
            decision = "monitor"
            root_cause = "Memory usage not above threshold now"
            confidence = 0.75

    elif alert_name == "PodCrashLoop":
        if restarts > 3 and error_logs:
            decision = "restart pod"
            root_cause = "CrashLoop pattern with recurring application errors"
            confidence = 0.95
        elif restarts > 3:
            decision = "investigate and restart pod"
            root_cause = "Frequent container restarts (CrashLoop pattern)"
            confidence = 0.9
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
        "decision_source": "rule-based-fallback",
        "recommended_by": "rule",
        "log_error_count": len(error_logs),
        "log_insights": logs[-5:],
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
    graph.add_node("collect_logs", collect_logs)
    graph.add_node("rca_analysis", rca_analysis)
    graph.add_node("decide_action", decide_action)

    graph.set_entry_point("analyze_alert")

    graph.add_edge("analyze_alert", "collect_metrics")
    graph.add_edge("collect_metrics", "collect_logs")
    graph.add_edge("collect_logs", "rca_analysis")
    graph.add_edge("rca_analysis", "decide_action")

    return graph.compile()