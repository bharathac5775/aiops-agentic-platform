from fastapi import FastAPI, Request
from datetime import datetime, timezone
import os
import time
import json
import uuid
import hashlib
from pathlib import Path
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException
from workflows.cpu_workflow import build_graph

workflow = build_graph()

app = FastAPI()

_core_v1_api = None
_apps_v1_api = None

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

ALLOWED_NAMESPACES = {
    ns.strip() for ns in os.getenv("REMEDIATION_ALLOWED_NAMESPACES", "default").split(",") if ns.strip()
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
        "allowed_actions": {"scale deployment", "restart pod"},
    },
    "PodCrashLoop": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODCRASHLOOP", 0.9),
        "allowed_actions": {"restart pod"},
    },
    "HighMemoryUsage": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_HIGHMEMORYUSAGE", 0.93),
        "allowed_actions": {"restart pod"},
    },
    "PodOOMKilled": {
        "min_confidence": _env_float("AUTO_MIN_CONFIDENCE_PODOOMKILLED", 0.95),
        "allowed_actions": set(),
    },
}


def _resolve_auto_remediation_mode():
    valid_modes = {"off", "dry-run", "safe-auto"}
    mode = AUTO_REMEDIATION_MODE

    if mode in valid_modes:
        return mode

    # Backward-compatible fallback for Day 14 config.
    return "safe-auto" if AUTO_REMEDIATE else "off"


def _should_auto_execute(action: str):
    mode = _resolve_auto_remediation_mode()
    normalized_action = _normalize_action(action)

    if mode == "off":
        return False, mode, True

    if mode == "dry-run":
        return True, mode, False

    # mode == safe-auto
    return normalized_action in SAFE_AUTO_ACTIONS, mode, normalized_action in SAFE_AUTO_ACTIONS


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
    should_run, auto_mode, execute_real = _should_auto_execute(normalized_action)

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


def _normalize_action(action: str | None):
    value = str(action or "").strip().lower()
    aliases = {
        "restart": "restart pod",
        "restart container": "restart pod",
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


def _execute_remediation(action: str, pod: str | None, namespace: str, deployment: str | None = None, replicas: int | None = None, dry_run: bool = False):
    normalized_action = _normalize_action(action)
    if normalized_action not in ALLOWED_ACTIONS:
        return {
            "status": "blocked",
            "reason": f"Action '{normalized_action}' is not allowed",
            "action": normalized_action,
        }

    if namespace not in ALLOWED_NAMESPACES:
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

            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "deployment": target_deployment,
                    "from_replicas": current,
                    "to_replicas": target,
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
            }

        if normalized_action == "increase memory limit and restart pod":
            # Day 14 safe implementation: restart pod immediately and defer resource mutation
            # policy to later automation step where memory target policy is introduced.
            if not pod:
                return {
                    "status": "blocked",
                    "reason": "pod is required for memory-limit-and-restart action",
                    "action": normalized_action,
                }
            if dry_run:
                return {
                    "status": "dry-run",
                    "action": normalized_action,
                    "namespace": namespace,
                    "pod": pod,
                    "note": "resource-limit patch deferred; pod restart would be executed",
                }

            core_v1_api.delete_namespaced_pod(name=pod, namespace=namespace)
            return {
                "status": "executed",
                "action": normalized_action,
                "namespace": namespace,
                "pod": pod,
                "note": "pod restarted; memory limit patch deferred to policy-driven step",
            }

        if normalized_action == "rollback deployment":
            # Kubernetes Python client has no stable rollout-undo equivalent in AppsV1 API.
            return {
                "status": "blocked",
                "action": normalized_action,
                "reason": "rollback deployment requires revision-aware rollout strategy and is deferred",
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
                "alert": alert
            })

            result = state.get("result", {})

            log("RESULT", f"{result}")

            decision = _evaluate_auto_policy(
                alert_name=alert_name,
                pod=result.get("pod") or pod_name,
                namespace=namespace,
                recommendation=result.get("recommendation"),
                confidence=result.get("confidence", 0),
            )

            if decision["run"]:
                remediation_response = _execute_remediation(
                    action=result.get("recommendation"),
                    pod=result.get("pod") or pod_name,
                    namespace=namespace,
                    deployment=result.get("deployment"),
                    replicas=result.get("target_replicas"),
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
                "alert": {
                    "labels": labels,
                    "startsAt": alert.get("startsAt"),
                    "endsAt": alert.get("endsAt"),
                    "fingerprint": alert.get("fingerprint"),
                },
                "remediation_attempts": remediation_attempts,
            }
            _persist_incident(incident_report)

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
            "alert": alert
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
    response["incident_id"] = saved_report["incident_id"]
    response["correlation_id"] = saved_report["correlation_id"]

    return response