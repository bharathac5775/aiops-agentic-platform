🤖 AIOps Agentic Self-Healing Kubernetes Platform

An AI-driven AIOps platform that monitors Kubernetes workloads, analyzes operational signals, and automatically performs remediation actions using Agentic AI workflows.

The system integrates observability tools, Kubernetes automation, and LLM-based reasoning to create a self-healing infrastructure environment.

🚀 Overview

Modern cloud infrastructure produces large volumes of metrics, logs, and alerts.
Operational issues often require manual investigation and remediation.

This project builds an automated incident response system that:

Monitors cluster health using observability tools

Detects anomalies through alerting rules

Uses AI to analyze metrics and logs

Determines the most appropriate remediation action

Executes fixes directly in Kubernetes

Generates incident reports for visibility

🏗 Architecture
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
🛠 Tech Stack
DevOps

GitHub

Docker

Kubernetes (Minikube)

Helm

Jenkins

Terraform

Ansible

Observability

Prometheus

Grafana

Alertmanager

Loki

Promtail

AI / Agentic AI

Python

FastAPI

LangGraph

LangChain

Ollama

ChromaDB

Streamlit

📂 Project Structure
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
⚙️ Environment Setup
Prerequisites

Install required tools:

Docker Desktop
Minikube
kubectl
Helm
Python 3.11+
Ollama

MacOS installation example:

brew install minikube kubectl helm ollama
☸️ Kubernetes Cluster

The platform runs on a local Kubernetes cluster using Minikube.

Start the cluster:

minikube start -p aiops --driver=docker --cpus=4 --memory=6144

Verify cluster:

kubectl get nodes
🧠 AI Engine Concept

The AI engine performs automated incident analysis and remediation.

Core workflow:

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

Supported remediation actions:

Restart Pod

Scale Deployment

Rollback Deployment

Ignore Alert

📊 Observability Stack

The observability layer collects operational data from the Kubernetes cluster.

Metrics

Prometheus

Visualization

Grafana

Alerts

Alertmanager

Logs

Loki + Promtail

📦 Development Environment

Create Python environment:

python3 -m venv venv
source venv/bin/activate

Install dependencies:

pip install fastapi langchain langgraph chromadb kubernetes streamlit
🔐 Security Notes

Avoid committing secrets or API keys.

Use .env files for sensitive configuration.

Ensure .env and local virtual environments are ignored in .gitignore.

Example:

.env
venv
__pycache__