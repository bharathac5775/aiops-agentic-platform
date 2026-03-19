from fastapi import FastAPI, Request
from datetime import datetime, timezone
import os
import time
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

_last_auto_action_ts = {}
_auto_action_attempts = {}


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

        if status != "firing":
            log("IGNORED", f"{alert_name} on pod {pod_name} (status={status})")
            ignored += 1
            continue

        log("PROCESSING", f"{alert_name} on pod {pod_name}")

        try:
            state = workflow.invoke({
                "alert": alert
            })

            result = state.get("result", {})

            log("RESULT", f"{result}")

            decision = _evaluate_auto_policy(
                alert_name=alert_name,
                pod=result.get("pod") or pod_name,
                namespace=(labels.get("namespace") or "default"),
                recommendation=result.get("recommendation"),
                confidence=result.get("confidence", 0),
            )

            if decision["run"]:
                remediation_response = _execute_remediation(
                    action=result.get("recommendation"),
                    pod=result.get("pod") or pod_name,
                    namespace=(labels.get("namespace") or "default"),
                    deployment=result.get("deployment"),
                    replicas=result.get("target_replicas"),
                    dry_run=not decision["execute_real"],
                )
                log(
                    "REMEDIATE",
                    f"mode={decision['mode']} decision={decision['reason']} response={remediation_response}",
                )
            else:
                log(
                    "REMEDIATE",
                    (
                        f"mode={decision['mode']} skipped action={decision['action']} "
                        f"reason={decision['reason']}"
                    ),
                )

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

    log("REMEDIATE", f"requested action={decision} pod={pod} namespace={namespace} deployment={deployment} dry_run={dry_run}")

    response = _execute_remediation(
        action=decision,
        pod=pod,
        namespace=namespace,
        deployment=deployment,
        replicas=replicas,
        dry_run=dry_run,
    )

    return response