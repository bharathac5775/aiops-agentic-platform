#!/usr/bin/env bash
set -euo pipefail

PROFILE="aiops"

for arg in "$@"; do
	case "$arg" in
		--profile=*)
			PROFILE="${arg#*=}"
			;;
		*)
			echo "Unknown argument: $arg"
			echo "Usage: $0 [--profile=<name>]"
			exit 1
			;;
	esac
done

if kubectl config get-contexts "$PROFILE" >/dev/null 2>&1; then
	kubectl config use-context "$PROFILE" >/dev/null
fi

kubectl get nodes >/dev/null 2>&1 || {
	echo "Kubernetes API is not reachable. Start minikube/profile first and retry reset."
	exit 1
}

echo "Uninstalling Helm releases from monitoring namespace..."
helm uninstall promtail -n monitoring || true
helm uninstall loki -n monitoring || true
helm uninstall monitoring -n monitoring || true

echo "Deleting application and alerts manifests..."
kubectl delete -f k8s/alerts/loki-alerts.yaml --ignore-not-found=true
kubectl delete -f k8s/alerts/cpu-alert.yaml --ignore-not-found=true
kubectl delete -f k8s/ai-engine-service.yaml --ignore-not-found=true
kubectl delete -f k8s/ai-engine-deployment.yaml --ignore-not-found=true
kubectl delete -f k8s/stress-app-service.yaml --ignore-not-found=true
kubectl delete -f k8s/stress-app-deployment.yaml --ignore-not-found=true

echo "Deleting Alertmanager custom secret (if present)..."
kubectl delete secret alertmanager-monitoring-kube-prometheus-alertmanager -n monitoring --ignore-not-found=true

echo "Waiting briefly for namespace resources to terminate..."
sleep 3

echo "Reset complete."
