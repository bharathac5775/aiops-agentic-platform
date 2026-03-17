# 🤖 AIOps Agentic Self-Healing Kubernetes Platform

An AI-driven AIOps platform that monitors Kubernetes workloads, analyzes operational signals, and automatically performs remediation actions using Agentic AI workflows.

The system integrates observability tools, Kubernetes automation, and LLM-based reasoning to create a self-healing infrastructure environment.

## 🚀 Overview

Modern cloud infrastructure produces large volumes of metrics, logs, and alerts.
Operational issues often require manual investigation and remediation.

This project builds an automated incident response system that:

- Monitors cluster health using observability tools
- Detects anomalies through alerting rules
- Uses AI to analyze metrics and logs
- Determines the most appropriate remediation action
- Executes fixes directly in Kubernetes
- Generates incident reports for visibility

## 🏗 Architecture

```text
                GitHub
                   │
                Jenkins
                   │
              Docker Build
                   │
               Kubernetes
               (Minikube)
                   │
        ┌──────────┼──────────┐
        │                     │
   Sample App           AI Engine
        │                     │
        │                     │
    Prometheus ─── Alertmanager
        │
      Grafana
        │
       Loki
        │
        ▼
     LangGraph Workflow
        │
        ▼
   Root Cause Analysis (LLM)
        │
        ▼
   Remediation Decision
        │
        ▼
  Kubernetes API Execution
```

## 🛠 Tech Stack

### DevOps

- GitHub
- Docker
- Kubernetes (Minikube)
- Helm
- Jenkins
- Terraform
- Ansible

### Observability

- Prometheus
- Grafana
- Alertmanager
- Loki
- Promtail

### AI / Agentic AI

- Python
- FastAPI
- LangGraph
- LangChain
- Ollama
- ChromaDB
- Streamlit

## 📂 Project Structure

```text
aiops-agentic-platform/

app/
  stress test application

ai-engine/
  agents/
  workflows/
  tools/
  api/

k8s/
  kubernetes manifests

jenkins/
  Jenkins pipeline

terraform/
  infrastructure setup

ansible/
  automation playbooks

dashboard/
  streamlit dashboard

docs/
  architecture documentation

README.md
```

## 🧪 Stress Test Application

A lightweight microservice used to simulate common production failures.
It generates CPU spikes, memory pressure, and application errors that will later be detected by the observability stack and analyzed by the AI engine.

### Endpoints

| Endpoint | Description |
|----------|-------------|
| /health | Application health check |
| /stats | Returns active stress simulation stats |
| /cpu-stress | Simulates CPU load (supports workers and iterations) |
| /memory-leak | Simulates memory growth (supports batches, chunk size, sleep) |
| /reset-memory | Clears simulated memory allocations |
| /error | Generates application error logs |
| /crash | Force exits process to simulate CrashLoopBackOff |

### Run Locally

```bash
cd app/src
python app.py
```

The service will run on:

```
http://localhost:5001
```

### Example

Trigger CPU stress:

```bash
curl "http://localhost:5001/cpu-stress?workers=2&iterations=80000000"
```

Trigger memory stress:

```bash
curl "http://localhost:5001/memory-leak?batches=50&chunk_size=200000&sleep_ms=200"
```

View internal stress counters:

```bash
curl http://localhost:5001/stats
```

System resource usage can be observed using:

```bash
top
```

This service will later be deployed in Kubernetes to generate test scenarios for the AIOps platform.

## 📦 Containerization

The stress test application is packaged as a Docker container for portability and deployment in Kubernetes.

### Build Image

```bash
docker build -t bacdocker/aiops-stress-app:v2 ./app
```

### Run Container

```bash
docker run -p 5001:5001 bacdocker/aiops-stress-app:v2
```

### Push to Docker Hub

```bash
docker push bacdocker/aiops-stress-app:v2
```

The container image will be used later when deploying the application to Kubernetes.

## ☸️ Kubernetes Deployment

The stress test application is deployed to a local Kubernetes cluster using Minikube.
The container image pushed to Docker Hub is used to create a Kubernetes Deployment and exposed using a NodePort Service.

### Deployment Steps

#### 1) Start Minikube Cluster

Start the cluster using the dedicated profile created for the project.

```bash
minikube start -p aiops --driver=docker --cpus=4 --memory=6144
```

Verify cluster status:

```bash
kubectl get nodes
```

Expected output:

```text
NAME    STATUS   ROLES           AGE   VERSION
aiops   Ready    control-plane
```

#### 2) Enable Required Minikube Addons

Certain Kubernetes features require additional components.

Enable metrics collection and ingress support:

```bash
minikube addons enable metrics-server -p aiops
minikube addons enable ingress -p aiops
```

Verify metrics-server pod:

```bash
kubectl get pods -n kube-system
```

Expected:

```text
metrics-server-xxxx   1/1   Running
```

Metrics server is required for:

```bash
kubectl top pods
kubectl top nodes
```

#### 3) Create Kubernetes Deployment

Deployment file:

`k8s/stress-app-deployment.yaml`

Apply the deployment:

```bash
kubectl apply -f k8s/stress-app-deployment.yaml
```

Verify deployment:

```bash
kubectl get deployments
kubectl get pods
```

Example output:

```text
stress-app-69f4d9c755-mgks2   1/1   Running
```

#### 4) Expose Application with Service

Service file:

`k8s/stress-app-service.yaml`

Apply the service:

```bash
kubectl apply -f k8s/stress-app-service.yaml
```

Verify service:

```bash
kubectl get svc
```

Example output:

```text
stress-app-service   NodePort   5001:30007/TCP
```

### Accessing the Application

Because Minikube runs inside Docker on macOS, direct NodePort access may not always work.
Minikube provides a helper command that creates a temporary tunnel.

Run:

```bash
minikube service stress-app-service -p aiops
```

Example output:

```text
http://127.0.0.1:53002
```

The terminal must remain open while the tunnel is active.

### Manual Application Testing

The application endpoints were tested using curl.

#### Health Check

```bash
curl http://127.0.0.1:<PORT>/health
```

Expected response:

```json
{"status":"healthy"}
```

#### CPU Stress Simulation

```bash
curl "http://127.0.0.1:<PORT>/cpu-stress?workers=2&iterations=80000000"
```

This triggers high CPU usage inside the pod.

#### Memory Stress Simulation

```bash
curl "http://127.0.0.1:<PORT>/memory-leak?batches=50&chunk_size=200000&sleep_ms=200"
```

This increases memory consumption inside the container.

#### Crash Simulation (for restart/CrashLoop testing)

```bash
curl http://127.0.0.1:<PORT>/crash
```

#### View Runtime Stats

```bash
curl http://127.0.0.1:<PORT>/stats
```

### Monitoring Pod Resource Usage

After enabling metrics-server:

```bash
kubectl top pods
```

Example output:

```text
NAME                          CPU(cores)   MEMORY(bytes)
stress-app-69f4d9c755-mgks2   1003m        205Mi
```

This confirms that the stress simulation generates observable resource usage in Kubernetes.

### Issues Encountered

#### Minikube Context Issue

In local setups with multiple clusters/profiles, `kubectl` may point to the wrong context.

Typical symptoms:

- `kubectl get pods` shows unexpected resources
- `kubectl top pods` fails unexpectedly
- service URLs or port-forward targets do not match deployed workloads

Verify current context:

```bash
kubectl config current-context
```

Switch to the project context:

```bash
kubectl config use-context aiops
```

Confirm cluster and profile health:

```bash
kubectl get nodes
minikube profile list
minikube status -p aiops
```

If the profile is stopped, start it:

```bash
minikube start -p aiops --driver=docker --cpus=4 --memory=6144
```

Ensure metrics addon is enabled:

```bash
minikube addons enable metrics-server -p aiops
```

Verify metrics-server pod:

```bash
kubectl get pods -n kube-system | grep metrics-server
```

Then re-check workloads:

```bash
kubectl get pods
kubectl get pods -n monitoring
```

### Result

The stress testing application is running inside the Kubernetes cluster and generating measurable CPU and memory load.

## ⚙️ Environment Setup

### Prerequisites

Install required tools:

- Docker Desktop
- Minikube
- kubectl
- Helm
- Python 3.11+
- Ollama

MacOS installation example:

```bash
brew install minikube kubectl helm ollama
```

## ☸️ Kubernetes Cluster

The platform runs on a local Kubernetes cluster using Minikube.

Start the cluster:

```bash
minikube start -p aiops --driver=docker --cpus=4 --memory=6144
```

Verify cluster:

```bash
kubectl get nodes
```

## 📈 Kubernetes Monitoring Setup with Prometheus and Grafana

### Objective

Set up a monitoring stack in Kubernetes to collect and visualize metrics from cluster workloads.
This setup enables observing resource usage such as CPU and memory from running pods and prepares the platform for alerting and AIOps analysis.

### Monitoring Stack Architecture

The monitoring stack consists of:

- **Prometheus** - collects and stores metrics from Kubernetes
- **Grafana** - visualizes metrics through dashboards
- **Node Exporter** - collects node-level metrics
- **kube-state-metrics** - exposes Kubernetes object metrics
- **Metrics Server** - provides resource metrics for pods and nodes

```text
Kubernetes Cluster
     │
     ▼
Prometheus (Metrics Collection)
     │
     ▼
Grafana (Visualization)
     │
     ▼
Dashboards showing CPU, memory, pod status
```

### Installing Prometheus Stack

The monitoring stack is installed using **Helm**, the standard package manager for Kubernetes.

#### Add Helm Repository

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
```

#### Install kube-prometheus-stack

```bash
helm install monitoring prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --create-namespace
```

This installs:

- Prometheus
- Grafana
- Alertmanager
- Node Exporter
- kube-state-metrics

### Verifying Installation

Check the deployed resources.

#### Verify Pods

```bash
kubectl get pods -n monitoring
```

Example output:

```text
monitoring-grafana
monitoring-kube-prometheus-operator
monitoring-kube-state-metrics
monitoring-prometheus-node-exporter
```

#### Verify Services

```bash
kubectl get svc -n monitoring
```

### Accessing Grafana

Forward the Grafana service to the local machine:

```bash
kubectl port-forward svc/monitoring-grafana 3000:80 -n monitoring
```

Open Grafana in the browser:

```text
http://localhost:3000
```

#### Default Credentials

```text
username: admin
password: for password run "kubectl --namespace monitoring get secrets monitoring-grafana -o jsonpath="{.data.admin-password}" | base64 -d ; echo"
```

### Verifying Metrics Collection

Prometheus automatically scrapes metrics from:

- Kubernetes nodes
- Running pods
- kube-state-metrics
- Node exporter

To confirm metrics are available:

```bash
kubectl top pods
```

Example output:

```text
NAME                          CPU(cores)   MEMORY(bytes)
stress-app-xxxxx              1000m        200Mi
```

This confirms that resource metrics are being collected.

### Visualizing Pod CPU Usage

A stress testing application was deployed earlier to simulate high CPU utilization.

When the stress endpoint is triggered:

```text
/cpu-stress
```

the application increases CPU consumption.

Prometheus collects these metrics and Grafana dashboards display the usage over time.

Example CPU query used in dashboards:

```promql
sum(rate(container_cpu_usage_seconds_total{namespace="default"}[5m])) by (pod)
```

This query calculates CPU usage for each pod over time.

### Dashboard Visualization

Grafana dashboards visualize:

- Pod CPU usage
- Pod memory consumption
- Node CPU utilization
- Node memory usage
- Pod restart count
- Pod status

These dashboards help monitor workload behavior and detect anomalies.

### Testing Monitoring with Stress Application

Trigger CPU load in the application:

```bash
curl "http://<service-url>/cpu-stress?workers=2&iterations=80000000"
```

Observe the metrics in Grafana dashboards where CPU utilization increases for the corresponding pod.

This confirms that the monitoring stack is correctly collecting and visualizing metrics.

### Outcome

The Kubernetes cluster is now equipped with a complete monitoring stack capable of:

- Collecting real-time metrics from nodes and pods
- Visualizing resource usage through Grafana dashboards
- Monitoring application behavior under load

This monitoring foundation enables alerting and automated response capabilities.

## 🚨 Alerting System Setup and Verification

### Objective

Kubernetes alerting is configured using Prometheus alert rules and Alertmanager to detect abnormal pod CPU usage.
The target condition is pod CPU usage above 80%, validating end-to-end anomaly detection.

### Alerting Flow

```text
Application Pod
↓
Prometheus collects metrics
↓
Prometheus evaluates alert rules
↓
Alertmanager receives alerts
↓
Alert is visible in Alertmanager UI
```

### Custom Alert Rule

Alert rule file:

`k8s/alerts/cpu-alert.yaml`

Key rule behavior:

- Alert name: `HighPodCPUUsage`
- Expression monitors pod CPU rate in `default` namespace
- Threshold: `> 0.8` (80% CPU)
- Duration: `for: 10s`
- Severity: `warning`

Example expression:

```promql
sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="default"}[2m])) > 0.8
```

### Deploy and Verify Rule

Apply rule:

```bash
kubectl apply -f k8s/alerts/cpu-alert.yaml
```

Verify rule object:

```bash
kubectl get prometheusrules -n monitoring
```

Expected to include:

```text
aiops-alert-rules
```

### Prometheus Verification

Port-forward Prometheus:

```bash
kubectl port-forward svc/monitoring-kube-prometheus-prometheus 9090 -n monitoring
```

UI:

```text
http://localhost:9090
```

Check rule in:

```text
Status → Rule Health
```

### Trigger and Validate Alert

Generate CPU stress:

```bash
curl "http://<service-ip>/cpu-stress?workers=2&iterations=80000000"
```

Validate resource usage:

```bash
kubectl top pods
kubectl top nodes
```

When CPU remains above threshold, alert status becomes:

```text
FIRING
```

Alert details:

- Alert: `HighPodCPUUsage`
- Pod: `stress-app`
- Severity: `warning`
- Typical observed value: `~1.0 CPU`

### Alertmanager Verification

Port-forward Alertmanager:

```bash
kubectl port-forward svc/monitoring-kube-prometheus-alertmanager 9093 -n monitoring
```

UI:

```text
http://localhost:9093
```

Active alert contains:

```text
summary: High CPU usage detected
description: Pod CPU usage above 80%
status: FIRING
```

### Result

Validated end-to-end alerting pipeline:

- Prometheus metrics collection
- Alert rule evaluation
- Alert firing
- Alertmanager reception
- Alert visibility in UI

This confirms the platform can automatically detect infrastructure anomalies.

## 🤖 AI Engine Integration with Alertmanager

### Objective

Integrate the AI engine with the observability stack so alerts generated by Prometheus are automatically delivered through Alertmanager webhooks.

This enables the platform to move from passive monitoring to AI-driven incident analysis. When an alert is triggered, the AI engine receives the payload, pulls live CPU metrics from Prometheus, and starts the automated investigation workflow.

### Architecture Flow

```text
Alertmanager → AI Engine (/alerts)
                         │
                         ▼
                  LangGraph Workflow
                         │
         ┌───────────────┼───────────────┐
         ▼               ▼               ▼
   Analyze Alert   Collect Metrics   Decide Action
                         │
                         ▼
                 Structured AI Output
```

### AI Engine Microservice

The AI engine is implemented as a FastAPI microservice responsible for receiving alerts and initiating investigation workflows.

Main responsibilities:

- Receive alerts from Alertmanager
- Parse alert payload
- Trigger LangGraph workflow
- Perform incident analysis
- Decide remediation actions

### AI Engine API

The AI engine exposes webhook endpoints used by Alertmanager.

#### Health Check Endpoint

`GET /`

Response:

```json
{
  "status": "AI Engine running"
}
```

#### Alert Webhook Endpoint

`POST /alerts`

This endpoint receives alert payloads from Alertmanager.

Example payload:

```json
{
  "alerts": [
    {
      "labels": {
     "alertname": "HighPodCPUUsage",
     "pod": "stress-app"
      }
    }
  ]
}
```

The AI engine extracts alert information and triggers the workflow.

#### Analyze Endpoint

`POST /analyze`

- Accepts alert payload
- Executes AI workflow
- Returns structured RCA response

Example response:

```json
{
     "analysis": {
          "root_cause": "High CPU saturation",
          "recommendation": "scale deployment",
          "confidence": 0.95
     }
}
```

#### Remediation Endpoint

`POST /remediate`

- Accepts remediation decision input
- Simulates remediation execution

Example response:

```json
{
     "status": "executed",
     "action": "scale deployment"
}
```

### Alert Processing Logic

When an alert is received:

- Alert payload is parsed
- Alert labels are extracted
- LangGraph workflow is invoked
- Investigation process begins

Example processing flow:

```text
Received Alert Payload
     │
     ▼
Extract Alert Name
     │
     ▼
Extract Pod Name
     │
     ▼
Invoke LangGraph Workflow
```

### LangGraph Workflow

The AI engine uses LangGraph to orchestrate alert investigation.

Workflow stages:

```text
Alert Received
      │
      ▼
Analyze Alert Context
      │
      ▼
Collect Metrics (Prometheus API)
      │
      ▼
Decide Remediation Strategy
```

Prometheus client used by workflow:

- File: `ai-engine/tools/prometheus_client.py`
- API endpoint: `http://monitoring-kube-prometheus-prometheus.monitoring:9090/api/v1/query`
- CPU query pattern: `sum(rate(container_cpu_usage_seconds_total{pod="<pod-name>"}[2m]))`

This modular flow allows expansion in later stages.

### Containerizing the AI Engine

The AI engine is packaged as a Docker container for Kubernetes deployment.

Build image:

```bash
docker build -t bacdocker/ai-engine:v7 ./ai-engine
```

Push image:

```bash
docker push bacdocker/ai-engine:v7
```

### Deploying AI Engine to Kubernetes

Deployment manifest:

`k8s/ai-engine-deployment.yaml`

Service manifest:

`k8s/ai-engine-service.yaml`

Service DNS:

`ai-engine.default.svc.cluster.local`

### Alertmanager Webhook Configuration

Alertmanager sends alerts to AI engine using webhook receiver.

Configuration file:

`k8s/alertmanager/alertmanager.yaml`

Example webhook config:

```yaml
receivers:
- name: ai-engine
  webhook_configs:
  - url: http://ai-engine.default.svc.cluster.local:8000/alerts
    send_resolved: true
```

Routing rule:

```yaml
routes:
- matchers:
     - alertname =~ "HighPodCPUUsage|HighMemoryUsage|PodCrashLoop|PodOOMKilled"
  receiver: ai-engine
```

### Prometheus Alert Rules

Prometheus now evaluates multiple operational risk conditions from a single rules file: `k8s/alerts/cpu-alert.yaml`.

Configured alerts:

- `HighPodCPUUsage`
- `HighMemoryUsage`
- `PodCrashLoop`
- `PodOOMKilled`

Example CPU expression:

```promql
sum(rate(container_cpu_usage_seconds_total{namespace="default"}[2m])) by (namespace, pod) > 0.8
```

### Testing the Integration

Test flow:

- Start the stress application
- Trigger CPU load
- Wait for Prometheus detection
- Alertmanager sends webhook
- AI engine receives alert

Example trigger:

```bash
curl "http://<stress-app-service>/cpu-stress?workers=2&iterations=80000000"
```

### Verifying AI Engine Execution

Monitor AI engine logs:

```bash
kubectl logs -l app=ai-engine -f
```

Expected output:

```text
Received Alert Payload
Analyzing alert: HighPodCPUUsage
Fetching CPU metrics for pod: <pod>
CPU Usage: <value>
Deciding remediation action
[METRICS] Pod=<pod> CPU=<value>
```

### Result

The system now supports automatic alert delivery from Alertmanager to AI engine, evolving the platform from monitoring-only to AI-assisted incident investigation.

Alerts generated in Kubernetes can now automatically initiate AI-based workflows.

## 🔁 One-Command Reproducible Setup

To keep the project fully declarative and repeatable, use the scripts in `scripts/`.

### Bootstrap (install/reconcile everything)

From project root:

```bash
./scripts/bootstrap.sh --start-minikube --profile=aiops
```

If Minikube is already running, you can run:

```bash
./scripts/bootstrap.sh --profile=aiops
```

What it does:

- Starts Minikube profile (optional via `--start-minikube`)
- Installs/updates Helm releases (`monitoring`, `loki`, `promtail`)
- Applies app manifests (`stress-app`, `ai-engine`)
- Applies alert rules (`cpu-alert`, `loki-alerts`)
- Reconciles Alertmanager config from `k8s/alertmanager/alertmanager.yaml`

### Reset (clean installed resources)

```bash
./scripts/reset.sh --profile=aiops
```

This removes Helm releases and applied manifests so the environment can be recreated cleanly.

Recommended full recreate sequence:

```bash
./scripts/reset.sh --profile=aiops
./scripts/bootstrap.sh --start-minikube --profile=aiops
```

## 🗂️ Complete Setup Guide (Loki, Alerting, AI Engine)

This section provides standard installation and configuration steps so a new user can set up logging, alerting, and AI-driven alert handling from scratch.

### 1) Loki + Promtail Installation (Helm)

Loki stores logs and Promtail collects logs from Kubernetes nodes/pods and ships them to Loki.

#### Files Used

- `k8s/loki/loki-values.yaml`
- `k8s/loki/promtail-values.yaml`

#### Install Steps

Add Grafana Helm repo:

```bash
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update
```

Install Loki in `monitoring` namespace:

```bash
helm upgrade --install loki grafana/loki \
  -n monitoring \
  -f k8s/loki/loki-values.yaml
```

Install Promtail in `monitoring` namespace:

```bash
helm upgrade --install promtail grafana/promtail \
  -n monitoring \
  -f k8s/loki/promtail-values.yaml
```

#### Verify Installation

```bash
kubectl get pods -n monitoring | egrep 'loki|promtail'
kubectl get svc -n monitoring | grep loki
```

Optional: verify logs in Grafana Explore (Datasource: Loki).

### 2) Prometheus Alert Rules Setup

Apply alert rules file:

```bash
kubectl apply -f k8s/alerts/cpu-alert.yaml
```

Verify rules:

```bash
kubectl get prometheusrules -n monitoring
```

Configured alerts include:

- `HighPodCPUUsage`
- `HighMemoryUsage`
- `PodCrashLoop`
- `PodOOMKilled`

Optional Loki safety alert file:

```bash
kubectl apply -f k8s/alerts/loki-alerts.yaml
```

### 3) Alertmanager Webhook Configuration

Alertmanager forwards selected alerts to the AI engine webhook endpoint.

Configuration file:

- `k8s/alertmanager/alertmanager.yaml`

Apply configuration:

```bash
kubectl apply -f k8s/alertmanager/alertmanager.yaml
```

Required webhook endpoint:

- `http://ai-engine.default.svc.cluster.local:8000/alerts`

Routing pattern for AIOps alerts:

- `alertname =~ "HighPodCPUUsage|HighMemoryUsage|PodCrashLoop|PodOOMKilled"`

### 4) AI Engine Build and Deployment

Build and push AI engine container image:

```bash
docker build -t bacdocker/ai-engine:v7 ./ai-engine
docker push bacdocker/ai-engine:v7
```

Deploy AI engine manifests:

```bash
kubectl apply -f k8s/ai-engine-deployment.yaml
kubectl apply -f k8s/ai-engine-service.yaml
kubectl rollout status deployment/ai-engine -n default
```

Verify running image:

```bash
kubectl get pods -n default -l app=ai-engine -o jsonpath='{.items[0].spec.containers[0].image}' && echo
```

### 5) End-to-End Validation

1. Trigger stress endpoint:

```bash
curl "http://<stress-app-service>/cpu-stress?workers=2&iterations=80000000"
```

2. Check alerts in Prometheus/Alertmanager.
3. Check AI engine logs:

```bash
kubectl logs -n default -l app=ai-engine -f
```

### Expected Platform Flow

```text
Stress App Issue
    ↓
Prometheus Rule Fires
    ↓
Alertmanager Routes Alert
    ↓
Webhook to AI Engine
    ↓
FastAPI receives alert
    ↓
LangGraph workflow runs
    ↓
Remediation decision
```

## 🧠 AI Engine Concept

The AI engine performs automated incident analysis and remediation.

Core workflow:

```text
Alert Received
     ↓
Collect Metrics
     ↓
Collect Logs
     ↓
LLM Root Cause Analysis
     ↓
Remediation Decision
     ↓
Execute Kubernetes Action
     ↓
Generate Incident Report
```

Supported remediation actions:

- Restart Pod
- Scale Deployment
- Rollback Deployment
- Ignore Alert

## 📊 Observability Stack

The observability layer collects operational data from the Kubernetes cluster.

- Metrics: Prometheus
- Visualization: Grafana
- Alerts: Alertmanager
- Logs: Loki + Promtail

## 📦 Development Environment

Create Python environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install fastapi langchain langgraph chromadb kubernetes streamlit
```

## 🔐 Security Notes

- Avoid committing secrets or API keys.
- Use `.env` files for sensitive configuration.
- Ensure `.env` and local virtual environments are ignored in `.gitignore`.

Example:

```gitignore
.env
venv
__pycache__
```
