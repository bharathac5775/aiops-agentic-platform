# 🤖 AIOps Agentic Self-Healing Kubernetes Platform

An AI-driven operations platform that transforms Kubernetes monitoring signals into actionable, guardrailed remediation decisions.

This project integrates observability systems, agentic reasoning, and Kubernetes-native controls to reduce manual incident response time, improve consistency of root-cause analysis, and make remediation safer through policy gates and execution safeguards.

For full extended project content, see [README-detailed.md](README-detailed.md).

## 🌟 Introduction

Modern platform operations teams receive high volumes of infrastructure alerts, but most alerts still require manual triage, context collection, and repetitive runbook execution. This creates alert fatigue, delayed recovery, and inconsistent incident handling quality.

This platform addresses that gap with an end-to-end AIOps workflow:
- ingest alerts in real time
- correlate metrics and logs automatically
- perform RCA using deterministic rules plus LLM and RAG context
- apply controlled remediation actions with Kubernetes safety checks
- persist incident history for auditability and future learning

The result is a practical, production-style operating model for self-healing Kubernetes workloads.

## 🧭 Overview

At a high level, the system works as follows:
1. Prometheus rules detect abnormal workload behavior.
2. Alertmanager sends firing alerts to the AI engine webhook.
3. A LangGraph multi-agent pipeline analyzes the incident context.
4. Policy and guardrails decide whether remediation can be executed.
5. Actions are applied through Kubernetes APIs (or safely skipped).
6. Incident artifacts are persisted and optionally pushed to Discord.

Core behavior implemented:
- Alert ingestion via POST /alerts
- Multi-agent orchestration: monitor -> rca -> remediate -> report (with fallback)
- Metrics and logs retrieval from Prometheus and Loki
- RAG-backed incident memory with Chroma
- Guardrailed remediation execution:
  - restart pod
  - scale deployment
  - increase memory limit and restart pod
  - rollback deployment (with retry-threshold safety checks)
- Persistent incident storage:
  - JSONL history
  - Markdown incident reports
- Operational dashboard for incident and remediation visibility

## 🏗️ System Architecture

```text
Stress App -> Prometheus Rules -> Alertmanager -> AI Engine (/alerts)
                                              -> monitor/rca/remediate/report
                                              -> Kubernetes API (guardrailed actions)
                                              -> Incident Store (JSONL + Markdown + Chroma)
                                              -> Discord (optional)
```

For full architecture diagrams and component-level explanation, see [docs/architecture.md](docs/architecture.md).

## 🚀 Key Capabilities

### 🧠 Intelligent Incident Analysis

- Combines alert labels, live metrics, and recent logs
- Uses LLM output with deterministic post-guardrails
- Enriches RCA with similarity retrieval from past incidents
- Falls back safely when signal quality is low or model output is invalid

### 🛡️ Guardrailed Auto-Remediation

- Supports policy-based action gating per alert category
- Enforces confidence thresholds and allowed-action maps
- Applies cooldown and retry-window controls to reduce flapping
- Restricts execution with namespace and action allowlists
- Integrates HPA-aware scaling behavior and rollback safety checks

### ✅ Operational Reliability

- Agent-level fallback chain on exceptions
- Non-blocking handling of Prometheus, Loki, RAG, and notification failures
- Traceable incident timeline with remediation attempt outcomes
- Durable incident artifacts for audits and postmortems

## 🛠️ Technology Stack

### ⚙️ Platform and Delivery

- Kubernetes (Minikube)
- Docker
- Helm
- Jenkins

### 📈 Observability

- Prometheus
- Alertmanager
- Grafana
- Loki
- Promtail

### 🤖 AI and Orchestration

- FastAPI
- LangGraph
- LangChain
- Ollama
- ChromaDB (RAG memory)

### 📊 Dashboard and Runtime

- Streamlit
- Python
- Kubernetes Python Client

## 📂 Repository Structure

```text
aiops-agentic-platform/
├── ai-engine/
├── app/
├── dashboard/
├── docs/
├── grafana/
├── jenkins/
├── k8s/
├── README.md
└── README-detailed.md
```

## 🚀 Quick Start

### 1) ☸️ Start cluster and enable addons

```bash
minikube start -p aiops --driver=docker --cpus=4 --memory=6144
minikube addons enable metrics-server -p aiops
minikube addons enable ingress -p aiops
```

### 2) 📈 Install observability stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace

helm upgrade --install loki grafana/loki \
  -n monitoring -f k8s/loki/loki-values.yaml

helm upgrade --install promtail grafana/promtail \
  -n monitoring -f k8s/loki/promtail-values.yaml
```

### 3) 📦 Deploy workloads and alert configuration

```bash
kubectl apply -f k8s/stress-app-deployment.yaml
kubectl apply -f k8s/stress-app-service.yaml
kubectl apply -f k8s/stress-app-hpa.yaml
kubectl apply -f k8s/ai-engine-rbac.yaml
kubectl apply -f k8s/ai-engine-incidents-pvc.yaml
kubectl apply -f k8s/ai-engine-deployment.yaml
kubectl apply -f k8s/ai-engine-service.yaml
kubectl apply -f k8s/alerts/cpu-alert.yaml
kubectl apply -f k8s/alerts/loki-alerts.yaml
```

### 4) 🔔 Configure Alertmanager webhook route

```bash
kubectl create secret generic alertmanager-monitoring-kube-prometheus-alertmanager \
  --from-file=alertmanager.yaml=k8s/alertmanager/alertmanager.yaml \
  -n monitoring --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart statefulset alertmanager-monitoring-kube-prometheus-alertmanager -n monitoring
```

### 5) 🩺 Verify runtime health

```bash
kubectl get pods -n default
kubectl get pods -n monitoring
kubectl get prometheusrule -n monitoring
```

### 6) 💬 Optional Discord notifications

Keep real webhook values out of Git.

```bash
kubectl -n default create secret generic ai-engine-discord-webhook \
  --from-literal=webhook-url='YOUR_REAL_WEBHOOK_URL' \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl rollout restart deployment/ai-engine -n default
kubectl rollout status deployment/ai-engine -n default --timeout=300s
```

## 🚨 Alert Coverage and Decisioning

Implemented alert classes include:
- HighPodCPUUsage
- HighMemoryUsage
- PodCrashLoop
- PodCrashLoopBackOff
- PodOOMKilled
- PodImagePullBackOff
- PodImagePullBackOffPersistent
- PodErrImagePull
- PodCreateContainerConfigError
- PodNotReadyTooLong

Decisioning combines:
- rule-based heuristics for deterministic baselines
- LLM RCA output when available
- guardrail overrides for safety-critical patterns
- alert-type policy maps with confidence thresholds

## 🛡️ Remediation Guardrail Model

Execution is constrained by runtime policy controls in AI engine:
- action allowlist
- namespace allowlist
- auto-remediation modes: off, dry-run, safe-auto
- cooldown and retry-window enforcement
- retry-limit protection
- HPA-aware scaling boundaries
- image-pull rollback retry threshold checks

This design keeps remediation useful while reducing accidental or unstable changes.

## 🧱 Deployment Footprint

Core manifests are maintained under [k8s](k8s):
- AI engine deployment/service/RBAC/PVC
- stress app deployment/service/HPA
- dashboard deployment/service/ingress
- alert rules and alertmanager webhook routing

Primary runtime services:
- AI Engine: [k8s/ai-engine-service.yaml](k8s/ai-engine-service.yaml)
- Stress App: [k8s/stress-app-service.yaml](k8s/stress-app-service.yaml)
- Dashboard: [k8s/dashboard-service.yaml](k8s/dashboard-service.yaml)

## 🔌 API Endpoints

Base service URL (in cluster):
- http://ai-engine.default.svc.cluster.local:8000

Available endpoints:
- GET /
- POST /alerts
- POST /analyze
- POST /remediate
- GET /incidents
- GET /incidents/{incident_id}
- GET /incidents/remediations
- GET /diagnostics/rag

## 📊 Dashboard

- Source: [dashboard/app.py](dashboard/app.py)
- In-cluster API base: AIOPS_API_BASE_URL=http://ai-engine.default.svc.cluster.local:8000

Run locally:

```bash
cd dashboard
pip install -r requirements.txt
streamlit run app.py
```

## 🔁 CI/CD

Pipeline definition: [jenkins/Jenkinsfile](jenkins/Jenkinsfile)

Pipeline stages:
- Checkout
- Quality Gates: Static Validation
- Build Docker Image
- Run Tests
- Push Docker Image
- Deploy to Kubernetes
- Smoke Check + Contract Gate

Required Jenkins credential:
- dockerhub-pass

Webhook secret provisioning is intentionally managed outside Jenkins.

## 🔐 Security and Governance

- Never commit secrets (plain or base64) to repository
- Keep [k8s/discord-webhook-secret.yaml](k8s/discord-webhook-secret.yaml) as placeholder template
- Rotate exposed webhook credentials immediately
- Prefer secret injection at runtime via kubectl or external secret manager

## 📚 Additional Documentation

- Full extended project content: [README-detailed.md](README-detailed.md)
- Detailed architecture and flow analysis: [docs/architecture.md](docs/architecture.md)
