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
from tools.rag import incident_memory_store

ALLOWED_RECOMMENDATIONS = {
    "scale deployment",
    "restart pod",
    "rollback deployment",
    "monitor",
    "investigate",
    "increase memory limit and restart pod",
    "no action",
    "investigate and restart pod",
}

IMAGE_PULL_ALERTS = {
    "PodImagePullBackOff",
    "PodErrImagePull",
}

PERSISTENT_IMAGE_PULL_ALERTS = {
    "PodImagePullBackOffPersistent",
}

CRASHLOOP_WAITING_ALERTS = {
    "PodCrashLoopBackOff",
}

CONFIG_ALERTS = {
    "PodCreateContainerConfigError",
}

READINESS_ALERTS = {
    "PodNotReadyTooLong",
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


def pre_decision_check(state):
    print("Running pre-decision checks")

    alert_name = state.get("alert_name")
    cpu = state.get("metrics", {}).get("cpu_usage", 0)

    # Fast path: for low CPU signals, skip log/LLM analysis.
    if alert_name == "HighPodCPUUsage" and 0 < cpu < 0.3:
        state["skip_llm"] = True
        state["decision"] = "no action"
        state["result"] = {
            "alert_name": alert_name,
            "pod": state.get("pod", "unknown"),
            "root_cause": "CPU spike recovered before analysis (transient condition)",
            "recommendation": "no action",
            "confidence": 0.95,
            "decision_source": "precheck-fast-path",
            "recommended_by": "rule",
            "guardrail_notes": ["llm skipped by precheck"],
            "reasoning_trace": {
                "used_metrics": True,
                "used_logs": False,
                "llm_used": False,
                "guardrails_applied": ["precheck-fast-path"],
            },
            "log_error_count": 0,
            "log_insights": [],
            "observed_metrics": state.get("metrics", {}),
            "similar_incidents": [],
        }
    else:
        state["skip_llm"] = False

    return state


def route_after_metrics(state):
    print("Routing after metrics")

    alert_name = state.get("alert_name")
    cpu = state.get("metrics", {}).get("cpu_usage", 0)
    memory = state.get("metrics", {}).get("memory_usage_bytes", 0)

    if alert_name in ["PodCrashLoop", "PodOOMKilled"]:
        return "collect_logs"

    if alert_name in CRASHLOOP_WAITING_ALERTS:
        return "collect_logs"

    if alert_name in IMAGE_PULL_ALERTS or alert_name in PERSISTENT_IMAGE_PULL_ALERTS or alert_name in CONFIG_ALERTS or alert_name in READINESS_ALERTS:
        return "collect_logs"

    if alert_name == "HighPodCPUUsage" and cpu > 0.7:
        return "collect_logs"

    if alert_name == "HighMemoryUsage" and memory > 300_000_000:
        return "collect_logs"

    return "rca_analysis"


def route_after_precheck(state):
    if state.get("skip_llm"):
        return "decide_action"
    return route_after_metrics(state)


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


def _normalize_llm_json(payload: dict | None):
    data = payload if isinstance(payload, dict) else {}
    if not data:
        return {}

    # Default missing keys to keep decision pipeline stable.
    if "root_cause" not in data:
        data["root_cause"] = "Unknown issue"
    if "recommendation" not in data:
        data["recommendation"] = "investigate"
    if "confidence" not in data:
        data["confidence"] = 0.7

    return data


def _build_similarity_query(alert_name: str, pod: str, metrics: dict, logs: list[str]) -> str:
    return (
        f"alert_name={alert_name}; "
        f"pod={pod}; "
        f"cpu={metrics.get('cpu_usage')}; "
        f"memory={metrics.get('memory_usage_bytes')}; "
        f"restarts={metrics.get('restart_count_5m')}; "
        f"oomkilled={metrics.get('oomkilled')}; "
        f"logs={' | '.join(logs[-3:])}"
    )


def _format_similar_incidents_context(items: list[dict]) -> str:
    if not items:
        return "None"

    lines = []
    for index, item in enumerate(items, start=1):
        metadata = item.get("metadata") or {}
        distance = item.get("distance")
        document = str(item.get("document") or "")
        truncated_doc = document[:400]
        lines.append(
            (
                f"{index}. incident_id={metadata.get('incident_id', 'unknown')} "
                f"alert={metadata.get('alert_name', 'unknown')} "
                f"recommendation={metadata.get('recommendation', 'unknown')} "
                f"distance={distance}\n"
                f"{truncated_doc}"
            )
        )

    return "\n\n".join(lines)


def rca_analysis(state):
    print("Running LLM-based RCA")

    alert_name = state.get("alert_name", "unknown")
    pod = state.get("pod", "unknown")
    metrics = state.get("metrics", {})
    logs = state.get("logs", [])[-10:]
    # Keep prompt context compact to reduce token overload and response drift.
    logs = [log[:200] for log in logs]

    similar_incidents = []
    try:
        similarity_query = _build_similarity_query(alert_name, pod, metrics, logs)
        similar_incidents = incident_memory_store.search_similar(similarity_query, limit=3)
    except Exception as error:
        print(f"[RAG] Similarity search failed: {error}")
        similar_incidents = []

    state["similar_incidents"] = similar_incidents
    similar_context = _format_similar_incidents_context(similar_incidents)

    prompt = f"""
You are a senior Kubernetes Site Reliability Engineer (SRE).

Your task is to perform root cause analysis (RCA) using:
- Alert information
- Metrics
- Logs

### Input:

Alert:
{alert_name}

Pod:
{pod}

Metrics:
- CPU cores: {metrics.get('cpu_usage')}
- Memory bytes: {metrics.get('memory_usage_bytes')}
- Restart count (5m): {metrics.get('restart_count_5m')}
- OOMKilled: {metrics.get('oomkilled')}

Logs (last 10 lines):
{logs}

Similar incidents from memory (for context, do not copy blindly):
{similar_context}

---

### Instructions:

1. Identify the most likely root cause.
2. Base reasoning ONLY on provided data.
3. Do NOT assume missing information.
4. Be concise and production-safe.
5. Use similar incidents as weak prior context only when they align with current metrics/logs.

---

### Allowed Recommendations:

- scale deployment
- restart pod
- rollback deployment
- monitor
- investigate
- increase memory limit and restart pod
- no action
- investigate and restart pod

---

### Output Rules:

- MUST return valid JSON only
- NO markdown
- NO explanation outside JSON
- Confidence must be between 0.0 and 1.0

---

### Output Format:
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
        llm_json = _extract_llm_json(response)
        state["llm_json"] = _normalize_llm_json(llm_json)

        if not state["llm_json"]:
            print("[LLM WARNING] Empty or invalid response")
    except Exception as e:
        print(f"LLM error: {e}")
        state["llm_output"] = ""
        state["llm_json"] = {}

    return state


def decide_action(state):
    print("Deciding remediation action")

    # Short-circuit when precheck already produced final result.
    if state.get("skip_llm") and state.get("result"):
        return state

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

        # Calibrate model confidence based on observed signal quality.
        if strong_cpu_timeout_signal or strong_oom_signal:
            confidence = max(confidence, 0.9)
        if cpu == 0 and memory == 0:
            confidence = min(confidence, 0.6)

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

        if alert_name == "HighPodCPUUsage" and cpu > 0.8 and recommendation != "scale deployment":
            recommendation = "scale deployment"
            root_cause = "Sustained high CPU saturation detected"
            confidence = max(confidence, 0.9)
            guardrail_notes.append("cpu-high-scale-only guardrail")

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

        if alert_name in IMAGE_PULL_ALERTS and recommendation in {"monitor", "no action", "scale deployment"}:
            recommendation = "investigate"
            root_cause = "Image pull failure detected; validate image tag, registry access, and pull secrets"
            confidence = max(confidence, 0.88)
            guardrail_notes.append("imagepull investigate-first guardrail")

        if alert_name in PERSISTENT_IMAGE_PULL_ALERTS and recommendation in {"monitor", "no action", "restart pod", "scale deployment", "investigate"}:
            recommendation = "rollback deployment"
            root_cause = "Persistent image pull failures across retries; rollback is safer than repeated restart"
            confidence = max(confidence, 0.93)
            guardrail_notes.append("persistent-imagepull rollback guardrail")

        if alert_name in CRASHLOOP_WAITING_ALERTS and recommendation in {"monitor", "no action"}:
            recommendation = "investigate and restart pod"
            root_cause = "CrashLoopBackOff waiting state detected"
            confidence = max(confidence, 0.9)
            guardrail_notes.append("crashloopbackoff restart guardrail")

        if alert_name in CONFIG_ALERTS and recommendation in {"monitor", "no action", "scale deployment"}:
            recommendation = "investigate"
            root_cause = "Container configuration error detected"
            confidence = max(confidence, 0.85)
            guardrail_notes.append("configerror investigate guardrail")

        if alert_name in READINESS_ALERTS and recommendation in {"monitor", "no action"}:
            recommendation = "restart pod"
            root_cause = "Pod remained not-ready beyond threshold"
            confidence = max(confidence, 0.88)
            guardrail_notes.append("notready restart guardrail")

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
            "reasoning_trace": {
                "used_metrics": True,
                "used_logs": len(logs) > 0,
                "llm_used": bool(llm_json),
                "guardrails_applied": guardrail_notes,
            },
            "log_error_count": len(error_logs),
            "log_insights": logs[-5:],
            "observed_metrics": {
                "cpu_usage": cpu,
                "memory_usage_bytes": memory,
                "restart_count_5m": restarts,
                "oomkilled": oomkilled,
            },
            "similar_incidents": state.get("similar_incidents", []),
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
        elif cpu > 0.9:
            decision = "scale deployment"
            root_cause = "Sustained high CPU saturation detected"
            confidence = 0.9
        elif cpu > 0.85 and timeout_logs:
            decision = "scale deployment"
            root_cause = "High CPU + timeout errors in logs"
            confidence = 0.95
        elif cpu > 0.85 and error_logs:
            decision = "scale deployment"
            root_cause = "High CPU with application errors in logs; prefer horizontal scaling"
            confidence = 0.92
        elif cpu > 0.85:
            decision = "scale deployment"
            root_cause = "High CPU without corroborating error logs; scale to absorb load"
            confidence = 0.9
        elif cpu > 0.7:
            decision = "scale deployment"
            root_cause = "Elevated CPU usage under active alert"
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

    elif alert_name in CRASHLOOP_WAITING_ALERTS:
        if error_logs:
            decision = "investigate and restart pod"
            root_cause = "CrashLoopBackOff with application error signals"
            confidence = 0.92
        else:
            decision = "restart pod"
            root_cause = "CrashLoopBackOff waiting state detected"
            confidence = 0.9

    elif alert_name == "PodOOMKilled":
        if oomkilled >= 1:
            decision = "increase memory limit and restart pod"
            root_cause = "Container was OOMKilled"
            confidence = 0.95
        else:
            decision = "investigate"
            root_cause = "OOMKilled alert fired but metric not present now"
            confidence = 0.65

    elif alert_name in IMAGE_PULL_ALERTS:
        decision = "investigate"
        root_cause = "Image pull failure indicates bad image/tag or registry authentication issue"
        confidence = 0.88

    elif alert_name in PERSISTENT_IMAGE_PULL_ALERTS:
        decision = "rollback deployment"
        root_cause = "Image pull failure persisted beyond retry threshold"
        confidence = 0.93

    elif alert_name in CONFIG_ALERTS:
        decision = "investigate"
        root_cause = "Container configuration error requires manifest/secret/config validation"
        confidence = 0.85

    elif alert_name in READINESS_ALERTS:
        if restarts > 0:
            decision = "restart pod"
            root_cause = "Pod not ready and showing instability"
            confidence = 0.88
        else:
            decision = "investigate"
            root_cause = "Pod stayed not-ready without restart signal"
            confidence = 0.8

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
        "reasoning_trace": {
            "used_metrics": True,
            "used_logs": len(logs) > 0,
            "llm_used": bool(llm_json),
            "guardrails_applied": [],
        },
        "log_error_count": len(error_logs),
        "log_insights": logs[-5:],
        "observed_metrics": {
            "cpu_usage": cpu,
            "memory_usage_bytes": memory,
            "restart_count_5m": restarts,
            "oomkilled": oomkilled,
        },
        "similar_incidents": state.get("similar_incidents", []),
    }

    return state


def build_graph():

    graph = StateGraph(dict)

    graph.add_node("analyze_alert", analyze_alert)
    graph.add_node("collect_metrics", collect_metrics)
    graph.add_node("pre_decision_check", pre_decision_check)
    graph.add_node("collect_logs", collect_logs)
    graph.add_node("rca_analysis", rca_analysis)
    graph.add_node("decide_action", decide_action)

    graph.set_entry_point("analyze_alert")

    graph.add_edge("analyze_alert", "collect_metrics")

    graph.add_edge("collect_metrics", "pre_decision_check")

    graph.add_conditional_edges(
        "pre_decision_check",
        route_after_precheck,
        {
            "collect_logs": "collect_logs",
            "rca_analysis": "rca_analysis",
            "decide_action": "decide_action",
        },
    )

    graph.add_edge("collect_logs", "rca_analysis")
    graph.add_edge("rca_analysis", "decide_action")

    return graph.compile()