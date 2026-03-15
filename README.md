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
| /cpu-stress | Simulates high CPU usage |
| /memory-leak | Simulates increasing memory usage |
| /error | Generates application error logs |

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
curl http://localhost:5001/cpu-stress
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
docker build -t bacdocker/aiops-stress-app:v1 ./app
```

### Run Container

```bash
docker run -p 5001:5001 bacdocker/aiops-stress-app:v1
```

### Push to Docker Hub

```bash
docker push bacdocker/aiops-stress-app:v1
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
curl http://127.0.0.1:<PORT>/cpu-stress
```

This triggers high CPU usage inside the pod.

#### Memory Stress Simulation

```bash
curl http://127.0.0.1:<PORT>/memory-leak
```

This increases memory consumption inside the container.

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

Initial commands failed due to incorrect Kubernetes context.

Fix:

```bash
kubectl config use-context aiops
```

#### Metrics API Not Available

`kubectl top pods` initially returned:

```text
error: Metrics API not available
```

Resolution:

Enable metrics-server:

```bash
minikube addons enable metrics-server -p aiops
```

Wait until the metrics-server pod becomes ready.

#### NodePort Not Reachable

Direct access using:

```text
http://<minikube-ip>:30007
```

did not work due to Docker networking limitations on macOS.

Solution:

Use Minikube service tunnel:

```bash
minikube service stress-app-service -p aiops
```

### Current Architecture

```text
Docker Hub
     ↓
Kubernetes Deployment
     ↓
Stress App Pod
     ↓
NodePort Service
     ↓
Minikube Tunnel
     ↓
Local Access
```

### Result

The stress testing application is now successfully running inside the Kubernetes cluster and generating measurable CPU and memory load. This prepares the environment for the next stage of the project: integrating the observability stack.

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