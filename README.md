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