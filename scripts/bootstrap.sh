#!/usr/bin/env bash
set -euo pipefail

PROFILE="aiops"
START_MINIKUBE=false

for arg in "$@"; do
  case "$arg" in
    --start-minikube)
      START_MINIKUBE=true
      ;;
    --profile=*)
      PROFILE="${arg#*=}"
      ;;
    *)
      echo "Unknown argument: $arg"
      echo "Usage: $0 [--start-minikube] [--profile=<name>]"
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
require_cmd helm

if command -v minikube >/dev/null 2>&1; then
  MINIKUBE_AVAILABLE=true
else
  MINIKUBE_AVAILABLE=false
fi

if [ "$START_MINIKUBE" = true ]; then
  if [ "$MINIKUBE_AVAILABLE" = false ]; then
    echo "minikube is required when --start-minikube is used"
    exit 1
  fi
  echo "[1/10] Starting minikube profile '$PROFILE'..."
  minikube start -p "$PROFILE" --driver=docker --cpus=4 --memory=6144
fi

if [ "$MINIKUBE_AVAILABLE" = true ]; then
  if minikube status -p "$PROFILE" >/dev/null 2>&1; then
    if ! minikube status -p "$PROFILE" | grep -q "host: Running"; then
      echo "[auto] Minikube profile '$PROFILE' is stopped. Starting it..."
      minikube start -p "$PROFILE" --driver=docker --cpus=4 --memory=6144
    fi
  fi
fi

echo "[2/10] Selecting kubectl context..."
if kubectl config get-contexts "$PROFILE" >/dev/null 2>&1; then
  kubectl config use-context "$PROFILE" >/dev/null
fi

echo "[2.1/10] Checking API connectivity..."
kubectl get nodes >/dev/null

echo "[3/10] Enabling minikube addons (if available)..."
if [ "$MINIKUBE_AVAILABLE" = true ] && minikube profile list -o json | grep -q "\"Name\":\"$PROFILE\""; then
  minikube addons enable metrics-server -p "$PROFILE" || true
  minikube addons enable ingress -p "$PROFILE" || true
fi

echo "[4/10] Adding Helm repositories..."
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null 2>&1 || true
helm repo add grafana https://grafana.github.io/helm-charts >/dev/null 2>&1 || true
helm repo update >/dev/null

echo "[5/10] Installing monitoring stack (kube-prometheus-stack)..."
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace

echo "[5.1/10] Waiting for monitoring operator and CRDs..."
kubectl rollout status deployment/monitoring-kube-prometheus-operator -n monitoring --timeout=300s
kubectl wait --for=condition=Established crd/prometheusrules.monitoring.coreos.com --timeout=300s
kubectl wait --for=condition=Established crd/alertmanagers.monitoring.coreos.com --timeout=300s

echo "[6/10] Installing Loki..."
helm upgrade --install loki grafana/loki \
  --namespace monitoring \
  -f k8s/loki/loki-values.yaml

echo "[7/10] Installing Promtail..."
helm upgrade --install promtail grafana/promtail \
  --namespace monitoring \
  -f k8s/loki/promtail-values.yaml

echo "[8/10] Deploying application workloads..."
kubectl apply -f k8s/stress-app-deployment.yaml
kubectl apply -f k8s/stress-app-service.yaml
kubectl apply -f k8s/stress-app-hpa.yaml
kubectl apply -f k8s/ai-engine-deployment.yaml
kubectl apply -f k8s/ai-engine-service.yaml

echo "[9/10] Applying alert rules and Alertmanager config..."
kubectl apply -f k8s/alerts/cpu-alert.yaml
kubectl apply -f k8s/alerts/loki-alerts.yaml

kubectl create secret generic alertmanager-monitoring-kube-prometheus-alertmanager \
  --from-file=alertmanager.yaml=k8s/alertmanager/alertmanager.yaml \
  -n monitoring \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart statefulset alertmanager-monitoring-kube-prometheus-alertmanager -n monitoring || true

echo "[10/10] Waiting for key components..."
kubectl rollout status deployment/ai-engine -n default --timeout=180s || true
kubectl rollout status deployment/stress-app -n default --timeout=180s || true
kubectl rollout status statefulset/loki -n monitoring --timeout=180s || true
kubectl rollout status daemonset/promtail -n monitoring --timeout=180s || true

echo "[10.1/10] Waiting for metrics API (metrics.k8s.io)..."
for i in $(seq 1 24); do
  if kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null | grep -q True; then
    break
  fi
  sleep 5
done

echo
echo "Bootstrap completed. Quick checks:"
echo "  kubectl get pods -n monitoring"
echo "  kubectl get pods -n default"
echo "  kubectl get prometheusrule -n monitoring"
echo "  helm list -n monitoring"
