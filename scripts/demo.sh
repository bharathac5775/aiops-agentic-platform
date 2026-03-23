#!/usr/bin/env bash
set -euo pipefail

PROFILE="aiops"
NAMESPACE="default"
TIMEOUT_SECONDS=420
POLL_SECONDS=10

for arg in "$@"; do
  case "$arg" in
    --profile=*) PROFILE="${arg#*=}" ;;
    --namespace=*) NAMESPACE="${arg#*=}" ;;
    --timeout=*) TIMEOUT_SECONDS="${arg#*=}" ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--profile=<name>] [--namespace=<name>] [--timeout=<seconds>]"
      exit 1
      ;;
  esac
done

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1"
    exit 1
  fi
}

require_cmd kubectl
require_cmd python3

if kubectl config get-contexts "$PROFILE" >/dev/null 2>&1; then
  kubectl config use-context "$PROFILE" >/dev/null
fi

kubectl get nodes >/dev/null

echo "[precheck] waiting for core deployments"
kubectl -n "$NAMESPACE" rollout status deployment/stress-app --timeout=300s
kubectl -n "$NAMESPACE" rollout status deployment/ai-engine --timeout=300s

cleanup_restore_memory() {
  echo "[cleanup] restoring stress-app deployment manifest"
  kubectl apply -f k8s/stress-app-deployment.yaml >/dev/null || true
  kubectl -n "$NAMESPACE" rollout status deployment/stress-app --timeout=300s >/dev/null || true
}
trap cleanup_restore_memory EXIT

run_incluster_curl() {
  local url="$1"
  local pod_name="demo-curl-$(date +%s%N)"

  kubectl -n "$NAMESPACE" run "$pod_name" \
    --image=curlimages/curl:8.7.1 \
    --restart=Never \
    --attach \
    --rm \
    --quiet \
    --command -- \
    sh -c "curl -fsS '$url'"
}

get_incidents_json() {
  run_incluster_curl "http://ai-engine.${NAMESPACE}.svc.cluster.local:8000/incidents?limit=200"
}

wait_for_incident() {
  local start_epoch="$1"
  local alerts_csv="$2"
  local label="$3"

  local deadline=$(( $(date +%s) + TIMEOUT_SECONDS ))

  echo "[wait] waiting for incident(s): $alerts_csv"

  while [ "$(date +%s)" -lt "$deadline" ]; do
    local payload
    payload="$(get_incidents_json || true)"

    if printf '%s' "$payload" | python3 - "$start_epoch" "$alerts_csv" <<'PY'
import json
import sys
from datetime import datetime, timezone

start_epoch = float(sys.argv[1])
expected = {item.strip() for item in sys.argv[2].split(',') if item.strip()}

raw = sys.stdin.read().strip()
if not raw:
    raise SystemExit(1)

try:
    data = json.loads(raw)
except Exception:
    raise SystemExit(1)

incidents = data.get("incidents", [])

for item in incidents:
    alert_name = str(item.get("alert_name", ""))
    created_at = str(item.get("created_at", ""))
    if not created_at:
        continue
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except Exception:
        continue

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    if dt.timestamp() >= start_epoch and alert_name in expected:
        print(f"matched incident={item.get('incident_id')} alert={alert_name} created_at={created_at}")
        raise SystemExit(0)

raise SystemExit(1)
PY
    then
      echo "[ok] ${label} incident observed"
      return 0
    fi

    sleep "$POLL_SECONDS"
  done

  echo "[error] timeout waiting for ${label} incident"
  return 1
}

trigger_cpu() {
  echo "[scenario] CPU saturation"
  run_incluster_curl "http://stress-app-service.${NAMESPACE}.svc.cluster.local:5001/reset-memory" >/dev/null || true
  run_incluster_curl "http://stress-app-service.${NAMESPACE}.svc.cluster.local:5001/cpu-stress?workers=4&iterations=200000000" >/dev/null
}

trigger_crashloop() {
  echo "[scenario] CrashLoop"
  for i in 1 2 3 4; do
    run_incluster_curl "http://stress-app-service.${NAMESPACE}.svc.cluster.local:5001/crash" >/dev/null || true
    sleep 4
  done
}

trigger_oom() {
  echo "[scenario] OOM"

  kubectl -n "$NAMESPACE" patch deployment stress-app --type='strategic' -p '{
    "spec": {
      "template": {
        "spec": {
          "containers": [
            {
              "name": "stress-app",
              "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "1500m", "memory": "256Mi"}
              }
            }
          ]
        }
      }
    }
  }' >/dev/null

  kubectl -n "$NAMESPACE" rollout status deployment/stress-app --timeout=300s

  run_incluster_curl "http://stress-app-service.${NAMESPACE}.svc.cluster.local:5001/memory-leak?batches=220&chunk_size=500000&sleep_ms=10" >/dev/null
}

start_cpu=$(date +%s)
trigger_cpu
wait_for_incident "$start_cpu" "HighPodCPUUsage" "CPU"

start_crash=$(date +%s)
trigger_crashloop
wait_for_incident "$start_crash" "PodCrashLoop,PodCrashLoopBackOff" "CrashLoop"

start_oom=$(date +%s)
trigger_oom
wait_for_incident "$start_oom" "PodOOMKilled" "OOM"

echo
echo "Day 20 deterministic demo completed successfully."
