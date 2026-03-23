# AIOps Platform Architecture

This document captures the implemented architecture and runtime control flow of the AIOps Agentic Self-Healing Kubernetes Platform.

## 1) End-to-End Architecture Flow

```mermaid
flowchart TD

%% =============================================================
%% SIGNAL SOURCES
%% =============================================================
subgraph SIG[Signal Sources]
  SA[Stress App Workload]
  PROM[Prometheus Metrics]
  LOKI[Loki Log Streams]
end

SA -->|resource and app behavior| PROM
SA -->|pod logs| LOKI

%% =============================================================
%% ALERTING PLANE
%% =============================================================
subgraph ALERT[Alerting Plane]
  RULES[PrometheusRule Set\nHighPodCPUUsage / HighMemoryUsage / CrashLoop / OOM / ...]
  AM[Alertmanager]
end

PROM --> RULES
RULES -->|firing alerts| AM

%% =============================================================
%% AIOPS ENGINE
%% =============================================================
subgraph AIOPS[AI Engine - FastAPI + LangGraph]
  API[API Layer\n/alerts /analyze /remediate /incidents /diagnostics/rag]
  ORCH[Agent Orchestrator\nLangGraph State Machine]

  subgraph AGENTS[Agent Chain]
    MON[Monitor Agent]
    RCA[RCA Agent]
    REM[Remediation Agent]
    REP[Report Agent]
    FB[Fallback Agent]
  end

  PRE[Precheck and Dynamic Routing]
  LLM[LLM Client - Ollama]
  RAG[RAG Service - Chroma Memory]
  POL[Policy and Guardrails\nmode + confidence + cooldown + retry]
  EXEC[Remediation Executor\nKubernetes API operations]
end

AM -->|Webhook POST /alerts| API
API --> ORCH

ORCH --> MON
MON -->|query| PROM
MON -->|query| LOKI
MON --> RCA

RCA --> PRE
PRE -->|fast path| POL
PRE -->|deep analysis path| LLM
PRE -->|historical similarity| RAG
LLM --> POL
RAG --> POL
POL --> REM

REM -->|approved action| EXEC
REM -->|blocked or degraded| FB
FB --> REP
EXEC --> REP

%% =============================================================
%% KUBERNETES EXECUTION TARGETS
%% =============================================================
subgraph K8S[Kubernetes Control Targets]
  PODS[Pods]
  DEP[Deployments]
  HPA[HorizontalPodAutoscaler]
  RS[ReplicaSets / Rollout History]
end

EXEC --> PODS
EXEC --> DEP
EXEC --> HPA
EXEC --> RS

%% =============================================================
%% PERSISTENCE + NOTIFICATIONS + VISUALIZATION
%% =============================================================
subgraph DATA[Persistence and Memory]
  HIST[Incident History JSONL]
  RPT[Incident Markdown Reports]
  VEC[Vector Memory Collection\nChromaDB]
end

REP --> HIST
REP --> RPT
REP --> VEC

subgraph OUT[External Outputs]
  DISC[Discord Notifications Optional]
  DASH[Streamlit Operations Dashboard]
  GRAF[Grafana Dashboards]
end

REP --> DISC
DASH -->|reads incidents and remediations APIs| API
PROM --> GRAF
LOKI --> GRAF

%% =============================================================
%% CI/CD DELIVERY LANE
%% =============================================================
subgraph CICD[CI/CD Delivery]
  GH[GitHub Repository]
  JN[Jenkins Pipeline]
  IMG[Container Images]
  DEPLOY[Manifest Apply and Rollout]
end

GH --> JN
JN --> IMG
IMG --> DEPLOY
DEPLOY --> K8S
```

## 2) Control Logic Highlights

### Agent Orchestration

- Runtime chain: monitor -> rca -> remediate -> report
- On agent error, execution routes through fallback path and still emits report artifacts

### Decision and Safety Model

- Recommendation source is hybrid: rules + LLM + RAG context
- Guardrails enforce:
  - action allowlist
  - namespace allowlist
  - auto-remediation mode (off, dry-run, safe-auto)
  - confidence thresholds by alert class
  - cooldown window and retry limits
- Additional protections:
  - HPA-aware scaling constraints
  - image-pull rollback threshold gating

### Persistence and Explainability

- Every processed incident stores:
  - incident record (JSONL)
  - markdown report artifact
  - remediation attempt history
  - agent trace timeline
- Incident memory is written to Chroma for future similarity retrieval

## 3) Operational Interfaces

### Ingress Interfaces

- Alertmanager webhook -> POST /alerts
- Manual RCA -> POST /analyze
- Manual remediation -> POST /remediate

### Query Interfaces

- GET /incidents
- GET /incidents/{incident_id}
- GET /incidents/remediations
- GET /diagnostics/rag

### Notification Interface

- Discord webhook via optional secret-backed configuration

## 4) Source Traceability

- API execution core: ai-engine/api/main.py
- Orchestration graph: ai-engine/workflows/agent_workflow.py
- RCA routing and guardrails: ai-engine/workflows/cpu_workflow.py
- Remediation policy and Kubernetes actions: ai-engine/api/main.py
- Notification adapter: ai-engine/tools/notification.py
- Alert routes: k8s/alertmanager/alertmanager.yaml
- Alert rules: k8s/alerts/cpu-alert.yaml
