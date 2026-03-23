from fastapi import FastAPI, Request
from datetime import datetime, timezone
import math
import os
import time
import json
import uuid
import hashlib
from pathlib import Path
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from workflows.agent_workflow import build_agent_graph
from tools.rag import incident_memory_store
from tools.notification import notify_discord_from_report

workflow = build_agent_graph()

app = FastAPI()

_core_v1_api = None
_apps_v1_api = None
_autoscaling_v2_api = None

ALLOWED_ACTIONS = {
    "restart pod",
    "scale deployment",
    "rollback deployment",
    "increase memory limit and restart pod",
}

SAFE_AUTO_ACTIONS = {
    "restart pod",
    "scale deployment",
}

IMAGE_PULL_ROLLBACK_ALERTS = {
    "PodImagePullBackOff",
    "PodErrImagePull",
    "PodImagePullBackOffPersistent",
}

PERSISTENT_IMAGE_PULL_ROLLBACK_ALERTS = {
    "PodImagePullBackOffPersistent",
}

IMAGE_PULL_RETRY_THRESHOLD = int(os.getenv("IMAGE_PULL_RETRY_THRESHOLD", "3"))

ALLOWED_NAMESPACES = {
    ns.strip() for ns in os.getenv("REMEDIATION_ALLOWED_NAMESPACES", "default,monitoring").split(",") if ns.strip()
}
AUTO_REMEDIATE = os.getenv("AUTO_REMEDIATE", "false").lower() == "true"
AUTO_REMEDIATION_MODE = os.getenv("AUTO_REMEDIATION_MODE", "off").strip().lower()
AUTO_REMEDIATE_COOLDOWN_SECONDS = int(os.getenv("AUTO_REMEDIATE_COOLDOWN_SECONDS", "300"))
AUTO_REMEDIATE_RETRY_WINDOW_SECONDS = int(os.getenv("AUTO_REMEDIATE_RETRY_WINDOW_SECONDS", "1800"))
AUTO_REMEDIATE_RETRY_LIMIT = int(os.getenv("AUTO_REMEDIATE_RETRY_LIMIT", "3"))
INCIDENT_STORE_DIR = Path(os.getenv("INCIDENT_STORE_DIR", "/tmp/ai-engine/incidents"))
INCIDENT_HISTORY_FILE = INCIDENT_STORE_DIR / "incidents.jsonl"
INCIDENT_REPORTS_DIR = INCIDENT_STORE_DIR / "reports"

_last_auto_action_ts = {}
_auto_action_attempts = {}


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def _ensure_incident_store():
    INCIDENT_STORE_DIR.mkdir(parents=True, exist_ok=True)
    INCIDENT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)


def _safe_json_dumps(payload):
    return json.dumps(payload, ensure_ascii=True, sort_keys=False)


def _build_correlation_id(alert: dict, default_namespace: str = "default"):
    labels = alert.get("labels") or {}
    fingerprint_source = {
        "alertname": labels.get("alertname", "unknown"),
        "pod": labels.get("pod", "unknown"),
        "namespace": labels.get("namespace", default_namespace),
        "startsAt": alert.get("startsAt", ""),
        "generatorURL": alert.get("generatorURL", ""),
    }
    digest = hashlib.sha1(_safe_json_dumps(fingerprint_source).encode("utf-8")).hexdigest()[:16]
    return f"corr-{digest}"


def _incident_markdown(report: dict):
    remediation_lines = []
    for attempt in report.get("remediation_attempts", []):
        remediation_lines.append(
            "| {timestamp} | {source} | {action} | {outcome} | {mode} | {reason} |".format(
                timestamp=attempt.get("timestamp", ""),
                source=attempt.get("source", ""),
                action=attempt.get("action", ""),
                outcome=attempt.get("outcome", ""),
                mode=attempt.get("mode", ""),
                reason=attempt.get("reason", ""),
            )
        )

    remediation_table = "\n".join(remediation_lines) if remediation_lines else "| - | - | - | - | - | - |"
    analysis = report.get("analysis") or {}
    observed = analysis.get("observed_metrics") or {}

    return (
        f"# Incident Report: {report.get('incident_id')}\n\n"
        f"- Correlation ID: {report.get('correlation_id')}\n"
        f"- Source: {report.get('source')}\n"
        f"- Status: {report.get('status')}\n"
        f"- Alert: {report.get('alert_name')}\n"
        f"- Namespace: {report.get('namespace')}\n"
        f"- Pod: {report.get('pod')}\n"
        f"- Created At: {report.get('created_at')}\n"
        f"- Completed At: {report.get('completed_at')}\n\n"
        "## Analysis\n\n"
        f"- Root Cause: {analysis.get('root_cause', 'n/a')}\n"
        f"- Recommendation: {analysis.get('recommendation', 'n/a')}\n"
        f"- Confidence: {analysis.get('confidence', 'n/a')}\n"
        f"- Decision Source: {analysis.get('decision_source', 'n/a')}\n\n"
        "## Observed Metrics\n\n"
        f"- CPU Usage: {observed.get('cpu_usage', 'n/a')}\n"
        f"- Memory Bytes: {observed.get('memory_usage_bytes', 'n/a')}\n"
        f"- Restarts (5m): {observed.get('restart_count_5m', 'n/a')}\n"
        f"- OOMKilled: {observed.get('oomkilled', 'n/a')}\n\n"
        "## Remediation Attempts\n\n"
        "| Timestamp | Source | Action | Outcome | Mode | Reason |\n"
        "|---|---|---|---|---|---|\n"
        f"{remediation_table}\n"
    )


def _persist_incident(report: dict):
    _ensure_incident_store()

    md_path = INCIDENT_REPORTS_DIR / f"{report['incident_id']}.md"
    report["report_markdown_path"] = str(md_path)

    with INCIDENT_HISTORY_FILE.open("a", encoding="utf-8") as fh:
        fh.write(_safe_json_dumps(report) + "\n")

    md_path.write_text(_incident_markdown(report), encoding="utf-8")
    return report


def _store_incident_memory(report: dict):
    try:
        incident_memory_store.store_incident(report)
    except Exception as error:
        log("RAG", f"memory write failed: {error}")


def _load_recent_incidents(limit: int = 20):
    _ensure_incident_store()
    if not INCIDENT_HISTORY_FILE.exists():
        return []

    rows = []
    with INCIDENT_HISTORY_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue

    return list(reversed(rows[-max(1, min(limit, 200)):]))


def _load_incident_by_id(incident_id: str):
    _ensure_incident_store()
    if not INCIDENT_HISTORY_FILE.exists():
        return None

    found = None
    with INCIDENT_HISTORY_FILE.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("incident_id") == incident_id:
                found = row
    return found


def _extract_remediation_history(limit: int = 50):
    incidents = _load_recent_incidents(limit=200)
    attempts = []
    for incident in incidents:
        for attempt in incident.get("remediation_attempts", []):
            attempts.append(
                {
                    "incident_id": incident.get("incident_id"),
                    "correlation_id": incident.get("correlation_id"),
                    "alert_name": incident.get("alert_name"),
                    "namespace": incident.get("namespace"),
                    "pod": incident.get("pod"),
                    **attempt,
                }
            )

    return attempts[-max(1, min(limit, 500)):][::-1]


def _get_rag_collection_count():
    collection = getattr(incident_memory_store, "_collection", None)
    if collection is None:
        return None

    try:
        return int(collection.count())
    except Exception:
        return None


def _build_rag_diagnostics():
    backend_name = incident_memory_store.__class__.__name__
    collection_count = _get_rag_collection_count()

    latest = _load_recent_incidents(limit=1)
    latest_incident = latest[0] if latest else None
    analysis = (latest_incident or {}).get("analysis") or {}
    similar_incidents = analysis.get("similar_incidents") or []

    top_match = similar_incidents[0] if similar_incidents else {}
    top_metadata = (top_match or {}).get("metadata") or {}

    return {
        "backend_name": backend_name,
        "collection_count": collection_count,
        "last_incident_id": (latest_incident or {}).get("incident_id"),
        "last_incident_alert_name": (latest_incident or {}).get("alert_name"),
        "last_incident_rag_hit_count": len(similar_incidents),
        "top_matched_incident_id": top_metadata.get("incident_id"),
        "top_matched_distance": (top_match or {}).get("distance"),
    }


def _env_float(name: str, default: float):
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


# Policy map by alert type for auto-remediation eligibility.
ALERT_POLICY = {
    "default": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_DEFAULT", 0.85),
        "allowed_actions": {"restart pod"},
    },
    "HighPodCPUUsage": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_HIGHPODCPUUSAGE", 0.9),
        "allowed_actions": {"scale deployment"},
    },
    "PodCrashLoop": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODCRASHLOOP", 0.9),
        "allowed_actions": {"restart pod"},
    },
    "PodCrashLoopBackOff": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODCRASHLOOPBACKOFF", 0.9),
        "allowed_actions": {"restart pod"},
    },
    "HighMemoryUsage": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_HIGHMEMORYUSAGE", 0.93),
        "allowed_actions": {"restart pod", "increase memory limit and restart pod"},
    },
    "PodOOMKilled": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODOOMKILLED", 0.95),
        "allowed_actions": set(),
    },
    "PodImagePullBackOff": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODIMAGEPULLBACKOFF", 0.9),
        "allowed_actions": set(),
    },
    "PodImagePullBackOffPersistent": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODIMAGEPULLBACKOFFPERSISTENT", 0.93),
        "allowed_actions": {"rollback deployment"},
    },
    "PodErrImagePull": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODERRIMAGEPULL", 0.9),
        "allowed_actions": set(),
    },
    "PodCreateContainerConfigError": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODCREATECONTAINERCONFIGERROR", 0.85),
        "allowed_actions": {"restart pod"},
    },
    "PodNotReadyTooLong": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODNOTREADYTOOLONG", 0.88),
        "allowed_actions": {"restart pod"},
    },
}


def _resolve_auto_remediation_mode():
    valid_modes = {"off", "dry-run", "safe-auto"}
    mode = AUTO_REMEDIATION_MODE

    if mode in valid_modes:
        return mode

    # Backward-compatible fallback for Day 14 config.
    return "safe-auto" if AUTO_REMEDIATE else "off"


def _should_auto_execute(action: str, alert_name: str | None = None):
    mode = _resolve_auto_remediation_mode()
    normalized_action = _normalize_action(action)

    if mode == "off":
        return False, mode, True

    if mode == "dry-run":
        return True, mode, False

    # mode == safe-auto
    if normalized_action in SAFE_AUTO_ACTIONS:
        return True, mode, True

    if normalized_action == "increase memory limit and restart pod" and alert_name == "HighMemoryUsage":
        return True, mode, True

    if normalized_action == "rollback deployment" and alert_name in PERSISTENT_IMAGE_PULL_ROLLBACK_ALERTS:
        return True, mode, True

    return False, mode, False


def _get_pod_max_restart_count_for_image_pull(core_v1_api, pod: str, namespace: str):
    pod_obj = core_v1_api.read_namespaced_pod(name=pod, namespace=namespace)
    statuses = list(pod_obj.status.container_statuses or []) + list(pod_obj.status.init_container_statuses or [])

    max_restart = 0
    for status in statuses:
        waiting_reason = ((status.state.waiting.reason if status.state and status.state.waiting else "") or "").strip()
        if waiting_reason in {"ImagePullBackOff", "ErrImagePull"}:
            max_restart = max(max_restart, int(status.restart_count or 0))

    return max_restart


def _prune_attempts(now_ts: float):
    min_ts = now_ts - AUTO_REMEDIATE_RETRY_WINDOW_SECONDS
    stale_keys = []

    for key, attempts in _auto_action_attempts.items():
        fresh = [ts for ts in attempts if ts >= min_ts]
        if fresh:
            _auto_action_attempts[key] = fresh
        else:
            stale_keys.append(key)

    for key in stale_keys:
        _auto_action_attempts.pop(key, None)


def _is_within_cooldown(alert_name: str, pod: str, namespace: str, action: str, now_ts: float):
    key = (alert_name, pod, namespace, action)
    previous_ts = _last_auto_action_ts.get(key)
    if previous_ts is None:
        return False
    return (now_ts - previous_ts) < AUTO_REMEDIATE_COOLDOWN_SECONDS


def _exceeds_retry_limit(alert_name: str, pod: str, namespace: str, action: str, now_ts: float):
    key = (alert_name, pod, namespace, action)
    attempts = _auto_action_attempts.get(key, [])
    return len(attempts) >= AUTO_REMEDIATE_RETRY_LIMIT


def _register_auto_attempt(alert_name: str, pod: str, namespace: str, action: str, now_ts: float):
    key = (alert_name, pod, namespace, action)
    _last_auto_action_ts[key] = now_ts
    attempts = _auto_action_attempts.get(key, [])
    attempts.append(now_ts)
    _auto_action_attempts[key] = attempts


def _evaluate_auto_policy(alert_name: str, pod: str, namespace: str, recommendation: str, confidence):
    normalized_action = _normalize_action(recommendation)
    should_run, auto_mode, execute_real = _should_auto_execute(normalized_action, alert_name=alert_name)

    if not should_run:
        return {
            "run": False,
            "mode": auto_mode,
            "execute_real": False,
            "reason": "mode-policy-block",
            "action": normalized_action,
        }

    try:
        confidence_value = float(confidence)
    except Exception:
        confidence_value = 0.0

    policy = ALERT_POLICY.get(alert_name, ALERT_POLICY["default"])
    min_confidence = float(policy.get("min_confidence", 0.85))
    allowed_actions = set(policy.get("allowed_actions", set()))

    if normalized_action not in allowed_actions:
        return {
            "run": False,
            "mode": auto_mode,
            "execute_real": False,
            "reason": "action-not-allowed-for-alert",
            "action": normalized_action,
        }

    if normalized_action == "rollback deployment" and alert_name in PERSISTENT_IMAGE_PULL_ROLLBACK_ALERTS:
        if not pod:
            return {
                "run": False,
                "mode": auto_mode,
                "execute_real": False,
                "reason": "missing-pod-for-imagepull-retry-check",
                "action": normalized_action,
            }

        try:
            core_v1_api, _ = _load_k8s_clients()
            retry_count = _get_pod_max_restart_count_for_image_pull(core_v1_api, pod=pod, namespace=namespace)
        except Exception as error:
            return {
                "run": False,
                "mode": auto_mode,
                "execute_real": False,
                "reason": f"imagepull-retry-check-failed:{error}",
                "action": normalized_action,
            }

        if retry_count < IMAGE_PULL_RETRY_THRESHOLD:
            return {
                "run": False,
                "mode": auto_mode,
                "execute_real": False,
                "reason": f"imagepull-retries-below-threshold({retry_count}<{IMAGE_PULL_RETRY_THRESHOLD})",
                "action": normalized_action,
            }

    if confidence_value < min_confidence:
        return {
            "run": False,
            "mode": auto_mode,
            "execute_real": False,
            "reason": f"confidence-below-threshold({confidence_value:.2f}<{min_confidence:.2f})",
            "action": normalized_action,
        }

    now_ts = time.time()
    _prune_attempts(now_ts)

    if _is_within_cooldown(alert_name, pod, namespace, normalized_action, now_ts):
        return {
            "run": False,
            "mode": auto_mode,
            "execute_real": False,
            "reason": "cooldown-active",
            "action": normalized_action,
        }

    if _exceeds_retry_limit(alert_name, pod, namespace, normalized_action, now_ts):
        return {
            "run": False,
            "mode": auto_mode,
            "execute_real": False,
            "reason": "retry-limit-reached",
            "action": normalized_action,
        }

    _register_auto_attempt(alert_name, pod, namespace, normalized_action, now_ts)
    return {
        "run": True,
        "mode": auto_mode,
        "execute_real": execute_real,
        "reason": "policy-pass",
        "action": normalized_action,
    }


def log(level: str, message: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] [{level}] {message}")


def _load_k8s_clients():
    global _core_v1_api
    global _apps_v1_api

    if _core_v1_api and _apps_v1_api:
        return _core_v1_api, _apps_v1_api

    try:
        config.load_incluster_config()
        log("K8S", "Loaded in-cluster Kubernetes config")
    except Exception:
        config.load_kube_config()
        log("K8S", "Loaded local kubeconfig")

    _core_v1_api = client.CoreV1Api()
    _apps_v1_api = client.AppsV1Api()
    return _core_v1_api, _apps_v1_api


def _load_autoscaling_v2_client():
    global _autoscaling_v2_api

    if _autoscaling_v2_api:
        return _autoscaling_v2_api

    # Reuse the same config bootstrap logic before creating additional clients.
    _load_k8s_clients()
    _autoscaling_v2_api = client.AutoscalingV2Api()
    return _autoscaling_v2_api


def _normalize_action(action: str | None):
    value = str(action or "").strip().lower()
    aliases = {
        "restart": "restart pod",
        "restart container": "restart pod",
        "investigate and restart pod": "restart pod",
        "scale": "scale deployment",
        "scale up": "scale deployment",
        "rollback": "rollback deployment",
    }
    return aliases.get(value, value)


def _infer_deployment_from_pod(pod_name: str | None):
    if not pod_name:
        return None
    parts = pod_name.split("-")
    # Typical deployment pod names: <deploy>-<replicaset>-<suffix>
    if len(parts) >= 3:
        return "-".join(parts[:-2])
    return None


def _namespace_allowed(namespace: str) -> bool:
    return namespace in ALLOWED_NAMESPACES


def _parse_memory_to_bytes(quantity: str | None) -> int:
    value = str(quantity or "").strip()
    if not value:
        raise ValueError("memory quantity is empty")

    binary_units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "Ei": 1024**6,
    }

    for suffix, multiplier in binary_units.items():
        if value.endswith(suffix):
            number = float(value[: -len(suffix)])
            return int(number * multiplier)

    return int(float(value))


def _format_bytes_to_mi(bytes_value: int) -> str:
    mebibyte = 1024**2
    target_mi = max(1, int(math.ceil(bytes_value / mebibyte)))
    return f"{target_mi}Mi"


def _looks_like_sidecar(container_name: str) -> bool:
    name = (container_name or "").strip().lower()
    if not name:
        return False

    sidecar_tokens = (
        "istio-proxy",
        "linkerd-proxy",
        "envoy",
        "fluent",
        "promtail",
        "datadog",
        "newrelic",
        "otel",
        "sidecar",
    )
    return any(token in name for token in sidecar_tokens)


def _pick_target_container_with_reason(deployment_obj, pod_obj=None):
    containers = (deployment_obj.spec.template.spec.containers or [])
    if not containers:
        return None, {
            "strategy": "none",
            "reason": "deployment has no containers",
        }

    preferred_name = os.getenv("REMEDIATION_MEMORY_TARGET_CONTAINER", "").strip()
    preferred_not_found = False
    if preferred_name:
        for container_obj in containers:
            if container_obj.name == preferred_name:
                return container_obj, {
                    "strategy": "explicit-env",
                    "reason": "matched REMEDIATION_MEMORY_TARGET_CONTAINER",
                    "selected_container": container_obj.name,
                    "selected_score": None,
                }
        preferred_not_found = True

    if len(containers) == 1:
        return containers[0], {
            "strategy": "single-container",
            "reason": "only one container available",
            "selected_container": containers[0].name,
            "selected_score": None,
        }

    statuses = []
    if pod_obj is not None and getattr(pod_obj, "status", None) is not None:
        statuses = list(pod_obj.status.container_statuses or []) + list(pod_obj.status.init_container_statuses or [])

    status_by_name = {status.name: status for status in statuses if getattr(status, "name", None)}

    best = None
    best_score = -10**9
    candidates = []

    for container_obj in containers:
        score = 0
        status = status_by_name.get(container_obj.name)

        if status is not None:
            score += int(status.restart_count or 0) * 10

            # Prefer the container that exhibits OOM symptoms.
            state = getattr(status, "state", None)
            last_state = getattr(status, "last_state", None)
            terminated = getattr(state, "terminated", None) or getattr(last_state, "terminated", None)
            waiting = getattr(state, "waiting", None)

            term_reason = (getattr(terminated, "reason", "") or "").strip()
            wait_reason = (getattr(waiting, "reason", "") or "").strip()

            if term_reason == "OOMKilled" or wait_reason == "OOMKilled":
                score += 100

        if _looks_like_sidecar(container_obj.name):
            score -= 25
        else:
            score += 5

        limits = (container_obj.resources.limits if container_obj.resources else {}) or {}
        if limits.get("memory"):
            score += 2

        candidates.append(
            {
                "container": container_obj.name,
                "score": score,
            }
        )

        if score > best_score:
            best = container_obj
            best_score = score

    if best is not None:
        return best, {
            "strategy": "scored-selection",
            "reason": (
                "selected highest score using pod signals, sidecar heuristic, and memory limits"
                if not preferred_not_found
                else "preferred container from REMEDIATION_MEMORY_TARGET_CONTAINER was not found; used scored-selection fallback"
            ),
            "selected_container": best.name,
            "selected_score": best_score,
            "candidates": candidates,
            "preferred_container": preferred_name if preferred_not_found else None,
        }

    fallback = containers[0]
    return fallback, {
        "strategy": "fallback-first",
        "reason": (
            "no scored candidate selected; defaulted to first container"
            if not preferred_not_found
            else "preferred container from REMEDIATION_MEMORY_TARGET_CONTAINER was not found; defaulted to first container"
        ),
        "selected_container": fallback.name,
        "selected_score": None,
        "preferred_container": preferred_name if preferred_not_found else None,
    }


def _compute_memory_target(current_limit: str):
    current_bytes = _parse_memory_to_bytes(current_limit)
    increment_percent = float(os.getenv("REMEDIATION_MEMORY_INCREMENT_PERCENT", "25"))
    max_limit_raw = os.getenv("REMEDIATION_MEMORY_MAX", "4Gi")
    max_bytes = _parse_memory_to_bytes(max_limit_raw)

    increased_bytes = int(current_bytes * (1 + (increment_percent / 100.0)))
    target_bytes = min(max_bytes, max(increased_bytes, current_bytes + 1024**2))

    return {
        "from": current_limit,
        "to": _format_bytes_to_mi(target_bytes),
        "from_bytes": current_bytes,
        "to_bytes": target_bytes,
        "max": max_limit_raw,
        "increment_percent": increment_percent,
    }


def _ensure_hpa_capacity(namespace: str, deployment_name: str, desired_replicas: int, dry_run: bool = False):
    autoscaling_v2_api = _load_autoscaling_v2_client()
    hpa_name = os.getenv("REMEDIATION_HPA_NAME", "").strip() or deployment_name
    max_cap = max(1, int(os.getenv("HPA_AUTO_MAX_CAP", "10")))
    increment = max(1, int(os.getenv("HPA_AUTO_MAX_INCREMENT", "1")))

    try:
        hpa = autoscaling_v2_api.read_namespaced_horizontal_pod_autoscaler(name=hpa_name, namespace=namespace)
    except ApiException as api_error:
        if api_error.status == 404:
            return {
                "hpa_found": False,
                "hpa_name": hpa_name,
                "reason": "hpa-not-found",
            }
        return {
            "hpa_found": False,
            "hpa_name": hpa_name,
            "reason": f"hpa-read-failed:{api_error.status}-{api_error.reason}",
        }

    target_ref = hpa.spec.scale_target_ref
    if (target_ref.kind or "") != "Deployment" or (target_ref.name or "") != deployment_name:
        return {
            "hpa_found": True,
            "hpa_name": hpa_name,
            "target_mismatch": True,
            "reason": "hpa-target-mismatch",
            "hpa_target_kind": target_ref.kind,
            "hpa_target_name": target_ref.name,
        }

    current_max = int(hpa.spec.max_replicas or 1)
    updated = False
    new_max = current_max

    if desired_replicas > current_max:
        new_max = min(max_cap, max(current_max + increment, desired_replicas))
        if new_max > current_max:
            if not dry_run:
                autoscaling_v2_api.patch_namespaced_horizontal_pod_autoscaler(
                    name=hpa_name,
                    namespace=namespace,
                    body={"spec": {"maxReplicas": new_max}},
                )
            updated = True

    return {
        "hpa_found": True,
        "hpa_name": hpa_name,
        "target_mismatch": False,
        "hpa_max_before": current_max,
        "hpa_max_after": new_max,
        "hpa_updated": updated,
        "hpa_max_cap": max_cap,
        "hpa_increment": increment,
    }


def _trigger_rollout_restart(apps_v1_api, deployment_name: str, namespace: str):
    body = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": _utc_now_iso(),
                    }
                }
            }
        }
    }
    apps_v1_api.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=body)


def _list_deployment_replicasets(apps_v1_api, deployment_obj, namespace: str):
    selector = deployment_obj.spec.selector.match_labels or {}
    selector_text = ",".join(f"{k}={v}" for k, v in selector.items()) if selector else None
    rs_list = apps_v1_api.list_namespaced_replica_set(namespace=namespace, label_selector=selector_text)

    owned = []
    for rs in rs_list.items:
        owners = rs.metadata.owner_references or []
        if any(owner.kind == "Deployment" and owner.name == deployment_obj.metadata.name for owner in owners):
            revision_raw = (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
            try:
                revision = int(revision_raw)
            except Exception:
                revision = 0
            owned.append((revision, rs))

    return sorted(owned, key=lambda item: item[0], reverse=True)


def _execute_remediation(action: str, pod: str | None, namespace: str, deployment: str | None = None, replicas: int | None = None, dry_run: bool = False, target_revision: int | None = None, alert_name: str | None = None):
    normalized_action = _normalize_action(action)
    if normalized_action not in ALLOWED_ACTIONS:
        return {
            "status": "blocked",
            "reason": f"Action '{normalized_action}' is not allowed",
            "action": normalized_action,
        }

    if not _namespace_allowed(namespace):
        return {
            "status": "blocked",
            "reason": f"Namespace '{namespace}' is not allowed",
            "action": normalized_action,
            "namespace": namespace,
        }

    core_v1_api, apps_v1_api = _load_k8s_clients()
    target_deployment = deployment or _infer_deployment_from_pod(pod)

    try:
        if normalized_action == "restart pod":
            if not pod:
                return {
                    "status": "blocked",
                    "reason": "pod is required for restart pod action",
                    "action": normalized_action,
                }
            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "pod": pod,
                }
            core_v1_api.delete_namespaced_pod(name=pod, namespace=namespace)
            return {
                "status": "executed",
                "action": normalized_action,
                "namespace": namespace,
                "pod": pod,
            }

        if normalized_action == "scale deployment":
            if not target_deployment:
                return {
                    "status": "blocked",
                    "reason": "deployment is required or inferable from pod for scale deployment action",
                    "action": normalized_action,
                }

            deployment_obj = apps_v1_api.read_namespaced_deployment(name=target_deployment, namespace=namespace)
            current = int(deployment_obj.spec.replicas or 1)
            target = int(replicas) if replicas is not None else min(current + 1, 10)
            target = max(1, target)
            hpa_capacity = _ensure_hpa_capacity(
                namespace=namespace,
                deployment_name=target_deployment,
                desired_replicas=target,
                dry_run=dry_run,
            )

            if hpa_capacity.get("hpa_found") and not hpa_capacity.get("target_mismatch"):
                hpa_max_after = int(hpa_capacity.get("hpa_max_after") or target)
                target = min(target, hpa_max_after)

            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "deployment": target_deployment,
                    "from_replicas": current,
                    "to_replicas": target,
                    "hpa": hpa_capacity,
                }

            body = {"spec": {"replicas": target}}
            apps_v1_api.patch_namespaced_deployment_scale(name=target_deployment, namespace=namespace, body=body)
            return {
                "status": "executed",
                "action": normalized_action,
                "namespace": namespace,
                "deployment": target_deployment,
                "from_replicas": current,
                "to_replicas": target,
                "hpa": hpa_capacity,
            }

        if normalized_action == "increase memory limit and restart pod":
            if not target_deployment:
                return {
                    "status": "blocked",
                    "reason": "deployment is required or inferable from pod for memory-limit-and-restart action",
                    "action": normalized_action,
                }

            deployment_obj = apps_v1_api.read_namespaced_deployment(name=target_deployment, namespace=namespace)
            pod_obj = None
            if pod:
                try:
                    pod_obj = core_v1_api.read_namespaced_pod(name=pod, namespace=namespace)
                except Exception:
                    pod_obj = None

            target_container, container_selection_reason = _pick_target_container_with_reason(deployment_obj, pod_obj=pod_obj)

            if target_container is None:
                return {
                    "status": "blocked",
                    "reason": "no containers found in target deployment",
                    "action": normalized_action,
                    "deployment": target_deployment,
                    "container_selection_reason": container_selection_reason,
                }

            current_limits = (target_container.resources.limits if target_container.resources else {}) or {}
            current_memory_limit = current_limits.get("memory")

            if not current_memory_limit:
                return {
                    "status": "blocked",
                    "reason": "target container has no memory limit to increase",
                    "action": normalized_action,
                    "deployment": target_deployment,
                    "container": target_container.name,
                    "container_selection_reason": container_selection_reason,
                }

            memory_target = _compute_memory_target(current_memory_limit)

            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "pod": pod,
                    "deployment": target_deployment,
                    "container": target_container.name,
                    "container_selection_reason": container_selection_reason,
                    "memory_from": memory_target["from"],
                    "memory_to": memory_target["to"],
                    "note": "deployment memory limit would be patched, then workload restarted",
                }

            body = {
                "spec": {
                    "template": {
                        "spec": {
                            "containers": [
                                {
                                    "name": target_container.name,
                                    "resources": {
                                        "limits": {
                                            "memory": memory_target["to"],
                                        }
                                    },
                                }
                            ]
                        }
                    }
                }
            }
            apps_v1_api.patch_namespaced_deployment(name=target_deployment, namespace=namespace, body=body)

            if pod:
                try:
                    core_v1_api.delete_namespaced_pod(name=pod, namespace=namespace)
                    restart_mode = "pod-delete"
                except ApiException as api_error:
                    # Deployment template was already patched. If the pod was replaced concurrently,
                    # continue with rollout restart instead of failing the entire remediation.
                    if api_error.status == 404:
                        _trigger_rollout_restart(apps_v1_api, target_deployment, namespace)
                        restart_mode = "deployment-rollout-restart(pod-not-found)"
                    else:
                        raise
            else:
                _trigger_rollout_restart(apps_v1_api, target_deployment, namespace)
                restart_mode = "deployment-rollout-restart"

            return {
                "status": "executed",
                "action": normalized_action,
                "namespace": namespace,
                "pod": pod,
                "deployment": target_deployment,
                "container": target_container.name,
                "container_selection_reason": container_selection_reason,
                "memory_from": memory_target["from"],
                "memory_to": memory_target["to"],
                "restart_mode": restart_mode,
            }

        if normalized_action == "rollback deployment":
            if alert_name in IMAGE_PULL_ROLLBACK_ALERTS:
                if not pod:
                    return {
                        "status": "blocked",
                        "action": normalized_action,
                        "reason": "pod is required for image-pull rollback safety check",
                    }
                try:
                    retry_count = _get_pod_max_restart_count_for_image_pull(core_v1_api, pod=pod, namespace=namespace)
                except Exception as error:
                    return {
                        "status": "blocked",
                        "action": normalized_action,
                        "reason": f"image-pull retry inspection failed: {error}",
                    }

                if retry_count < IMAGE_PULL_RETRY_THRESHOLD:
                    return {
                        "status": "blocked",
                        "action": normalized_action,
                        "reason": f"image-pull retries below threshold ({retry_count}<{IMAGE_PULL_RETRY_THRESHOLD})",
                    }

            if not target_deployment:
                return {
                    "status": "blocked",
                    "action": normalized_action,
                    "reason": "deployment is required or inferable from pod for rollback",
                }

            deployment_obj = apps_v1_api.read_namespaced_deployment(name=target_deployment, namespace=namespace)
            revision_raw = (deployment_obj.metadata.annotations or {}).get("deployment.kubernetes.io/revision")
            try:
                current_revision = int(revision_raw)
            except Exception:
                current_revision = 0

            rs_revisions = _list_deployment_replicasets(apps_v1_api, deployment_obj, namespace)
            if not rs_revisions:
                return {
                    "status": "blocked",
                    "action": normalized_action,
                    "reason": "no rollout history found for deployment",
                    "deployment": target_deployment,
                }

            if target_revision is not None:
                desired_revision = int(target_revision)
            else:
                desired_revision = 0
                for revision, _ in rs_revisions:
                    if current_revision and revision < current_revision:
                        desired_revision = revision
                        break

            if desired_revision <= 0:
                return {
                    "status": "blocked",
                    "action": normalized_action,
                    "reason": "no previous revision available to roll back",
                    "deployment": target_deployment,
                    "current_revision": current_revision,
                }

            target_rs = None
            for revision, rs in rs_revisions:
                if revision == desired_revision:
                    target_rs = rs
                    break

            if target_rs is None:
                return {
                    "status": "blocked",
                    "action": normalized_action,
                    "reason": f"target revision {desired_revision} not found in rollout history",
                    "deployment": target_deployment,
                    "current_revision": current_revision,
                }

            api_client = client.ApiClient()
            target_template = api_client.sanitize_for_serialization(target_rs.spec.template)

            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "deployment": target_deployment,
                    "from_revision": current_revision,
                    "to_revision": desired_revision,
                }

            body = {
                "spec": {
                    "template": target_template,
                }
            }
            apps_v1_api.patch_namespaced_deployment(name=target_deployment, namespace=namespace, body=body)

            return {
                "status": "executed",
                "action": normalized_action,
                "namespace": namespace,
                "deployment": target_deployment,
                "from_revision": current_revision,
                "to_revision": desired_revision,
            }

        return {
            "status": "blocked",
            "action": normalized_action,
            "reason": "Unhandled remediation action",
        }

    except ApiException as api_error:
        return {
            "status": "failed",
            "action": normalized_action,
            "namespace": namespace,
            "error": f"Kubernetes API error: {api_error.status} {api_error.reason}",
        }
    except Exception as error:
        return {
            "status": "failed",
            "action": normalized_action,
            "namespace": namespace,
            "error": str(error),
        }


@app.get("/")
def health():
    return {"status": "AI Engine running"}


@app.get("/incidents")
def list_incidents(limit: int = 20):
    incidents = _load_recent_incidents(limit=limit)
    return {
        "incidents": incidents,
        "count": len(incidents),
    }


@app.get("/incidents/remediations")
def list_remediation_history(limit: int = 50):
    history = _extract_remediation_history(limit=limit)
    return {
        "remediation_history": history,
        "count": len(history),
    }


@app.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    incident = _load_incident_by_id(incident_id)
    if not incident:
        return {"error": "Incident not found", "incident_id": incident_id}
    return incident


@app.get("/diagnostics/rag")
def rag_diagnostics():
    return _build_rag_diagnostics()


@app.post("/alerts")
async def receive_alert(request: Request):

    payload = await request.json()

    if not isinstance(payload, dict):
        log("IGNORED", "Invalid payload type")
        return {"message": "Invalid payload", "processed": 0, "ignored": 0, "failed": 0}

    alerts = payload.get("alerts", [])

    if not isinstance(alerts, list):
        log("IGNORED", "Invalid alerts format")
        return {"message": "Invalid alerts format", "processed": 0, "ignored": 0, "failed": 0}

    log("RECEIVED", f"{len(alerts)} alert(s) from Alertmanager")

    processed = 0
    ignored = 0
    failed = 0

    for alert in alerts:
        status = alert.get("status")
        labels = alert.get("labels") or {}
        alert_name = labels.get("alertname", "unknown")
        pod_name = labels.get("pod", "unknown")
        namespace = labels.get("namespace") or "default"
        incident_id = f"inc-{uuid.uuid4().hex[:12]}"
        correlation_id = _build_correlation_id(alert, default_namespace=namespace)
        remediation_attempts = []

        if status != "firing":
            log("IGNORED", f"{alert_name} on pod {pod_name} (status={status})")
            ignored += 1
            continue

        log("PROCESSING", f"{alert_name} on pod {pod_name}")
        incident_started_at = _utc_now_iso()

        try:
            state = workflow.invoke({
                "alert": alert,
                "evaluate_auto_policy_fn": _evaluate_auto_policy,
                "execute_remediation_fn": _execute_remediation,
                "analysis_only": False,
            })

            result = state.get("result", {})

            log("RESULT", f"{result}")

            decision = state.get("auto_policy_decision")
            remediation_attempt = state.get("remediation_attempt")

            # Compatibility fallback: preserve old behavior if agent callbacks were bypassed.
            if not isinstance(decision, dict):
                decision = _evaluate_auto_policy(
                    alert_name=alert_name,
                    pod=result.get("pod") or pod_name,
                    namespace=namespace,
                    recommendation=result.get("recommendation"),
                    confidence=result.get("confidence", 0),
                )

            if isinstance(remediation_attempt, dict):
                remediation_attempts.append(remediation_attempt)
                log("REMEDIATE", f"agent-chain attempt={remediation_attempt}")
            else:
                if decision["run"]:
                    remediation_response = _execute_remediation(
                        action=result.get("recommendation"),
                        pod=result.get("pod") or pod_name,
                        namespace=namespace,
                        deployment=result.get("deployment"),
                        replicas=result.get("target_replicas"),
                        alert_name=alert_name,
                        dry_run=not decision["execute_real"],
                    )
                    remediation_attempts.append(
                        {
                            "timestamp": _utc_now_iso(),
                            "source": "auto-policy",
                            "action": result.get("recommendation"),
                            "mode": decision["mode"],
                            "reason": decision["reason"],
                            "outcome": remediation_response.get("status", "unknown"),
                            "response": remediation_response,
                        }
                    )
                    log(
                        "REMEDIATE",
                        f"mode={decision['mode']} decision={decision['reason']} response={remediation_response}",
                    )
                else:
                    remediation_attempts.append(
                        {
                            "timestamp": _utc_now_iso(),
                            "source": "auto-policy",
                            "action": decision.get("action"),
                            "mode": decision["mode"],
                            "reason": decision["reason"],
                            "outcome": "skipped",
                            "response": decision,
                        }
                    )
                    log(
                        "REMEDIATE",
                        (
                            f"mode={decision['mode']} skipped action={decision['action']} "
                            f"reason={decision['reason']}"
                        ),
                    )

            incident_report = {
                "incident_id": incident_id,
                "correlation_id": correlation_id,
                "source": "alertmanager-webhook",
                "status": "processed",
                "alert_status": status,
                "alert_name": alert_name,
                "namespace": namespace,
                "pod": result.get("pod") or pod_name,
                "created_at": incident_started_at,
                "completed_at": _utc_now_iso(),
                "analysis": result,
                "decision": decision,
                "agent_trace": state.get("agent_trace", []),
                "agent_error": state.get("agent_error"),
                "alert": {
                    "labels": labels,
                    "startsAt": alert.get("startsAt"),
                    "endsAt": alert.get("endsAt"),
                    "fingerprint": alert.get("fingerprint"),
                },
                "remediation_attempts": remediation_attempts,
            }
            saved_report = _persist_incident(incident_report)
            _store_incident_memory(saved_report)
            notify_discord_from_report(saved_report)

            processed += 1

        except Exception as error:
            log("FAILED", f"{alert_name} on pod {pod_name}: {error}")
            failed += 1

    log("SUMMARY", f"processed={processed} ignored={ignored} failed={failed}")

    return {
        "message": "Alert received",
        "processed": processed,
        "ignored": ignored,
        "failed": failed
    }


#  Analyze API
@app.post("/analyze")
async def analyze(request: Request):

    data = await request.json()

    alert = data.get("alert")

    log("ANALYZE", "Running RCA workflow")

    try:
        state = workflow.invoke({
            "alert": alert,
            "analysis_only": True,
        })

        result = state.get("result", {})

        return {
            "analysis": result
        }

    except Exception as e:
        log("ERROR", f"Analyze failed: {e}")
        return {"error": str(e)}


# Remediation API
@app.post("/remediate")
async def remediate(request: Request):

    data = await request.json()
    decision = data.get("decision") or data.get("action")
    pod = data.get("pod")
    namespace = data.get("namespace", "default")
    deployment = data.get("deployment")
    replicas = data.get("replicas")
    target_revision = data.get("target_revision")
    alert_name = data.get("alert_name")
    dry_run = bool(data.get("dry_run", False))
    incident_started_at = _utc_now_iso()

    log("REMEDIATE", f"requested action={decision} pod={pod} namespace={namespace} deployment={deployment} dry_run={dry_run}")

    response = _execute_remediation(
        action=decision,
        pod=pod,
        namespace=namespace,
        deployment=deployment,
        replicas=replicas,
        dry_run=dry_run,
        target_revision=target_revision,
        alert_name=alert_name,
    )

    incident_report = {
        "incident_id": f"inc-{uuid.uuid4().hex[:12]}",
        "correlation_id": data.get("correlation_id") or f"corr-{uuid.uuid4().hex[:10]}",
        "source": "manual-remediation-api",
        "status": "processed",
        "alert_status": "manual",
        "alert_name": data.get("alert_name") or "manual-remediation",
        "namespace": namespace,
        "pod": pod,
        "created_at": incident_started_at,
        "completed_at": _utc_now_iso(),
        "analysis": {
            "root_cause": data.get("root_cause") or "manual invocation",
            "recommendation": decision,
            "confidence": data.get("confidence") or "manual",
            "decision_source": "manual",
        },
        "decision": {
            "run": True,
            "mode": "manual",
            "execute_real": not dry_run,
            "reason": "manual-remediation-endpoint",
            "action": _normalize_action(decision),
        },
        "alert": {
            "labels": {
                "namespace": namespace,
                "pod": pod,
            }
        },
        "remediation_attempts": [
            {
                "timestamp": _utc_now_iso(),
                "source": "manual-remediation-api",
                "action": decision,
                "mode": "manual",
                "reason": "manual-remediation-endpoint",
                "outcome": response.get("status", "unknown"),
                "response": response,
            }
        ],
    }
    saved_report = _persist_incident(incident_report)
    _store_incident_memory(saved_report)
    notify_discord_from_report(saved_report)
    response["incident_id"] = saved_report["incident_id"]
    response["correlation_id"] = saved_report["correlation_id"]

    return response