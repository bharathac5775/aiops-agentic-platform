from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    alert: dict

    # extracted
    alert_name: str
    pod: str
    namespace: str

    # observability
    metrics: dict
    logs: list[str]

    # RAG
    similar_incidents: list[dict]

    # analysis
    llm_output: str
    llm_json: dict

    # decision
    decision: str
    result: dict

    # remediation
    auto_policy_decision: dict
    remediation_response: dict
    remediation_attempt: dict

    # report
    incident_report: dict

    # orchestration
    agent_trace: list[dict]
    agent_error: dict
    analysis_only: bool

    # runtime callbacks passed from API layer
    evaluate_auto_policy_fn: Any
    execute_remediation_fn: Any
