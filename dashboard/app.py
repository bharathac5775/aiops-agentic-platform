import os
from collections import Counter
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

DEFAULT_BASE_URL = os.getenv("AIOPS_API_BASE_URL", "http://localhost:18000")
REQUEST_TIMEOUT_SECONDS = 8


@st.cache_data(ttl=10)
def fetch_json(url: str):
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json(), None
    except Exception as error:
        return None, str(error)


def to_datetime(value):
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def format_incidents_dataframe(incidents):
    rows = []
    for item in incidents:
        analysis = item.get("analysis") or {}
        decision = item.get("decision") or {}
        rows.append(
            {
                "incident_id": item.get("incident_id"),
                "created_at": item.get("created_at"),
                "alert_name": item.get("alert_name"),
                "namespace": item.get("namespace"),
                "pod": item.get("pod"),
                "recommendation": analysis.get("recommendation"),
                "decision_source": analysis.get("decision_source"),
                "confidence": analysis.get("confidence"),
                "auto_mode": decision.get("mode"),
                "auto_reason": decision.get("reason"),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df.fillna("")


def format_remediations_dataframe(remediations):
    if not remediations:
        return pd.DataFrame()

    df = pd.DataFrame(remediations)
    preferred_columns = [
        "timestamp",
        "incident_id",
        "alert_name",
        "namespace",
        "pod",
        "source",
        "action",
        "mode",
        "reason",
        "outcome",
    ]
    ordered = [col for col in preferred_columns if col in df.columns]
    leftovers = [col for col in df.columns if col not in ordered]
    return df[ordered + leftovers].fillna("")


def latest_rca_cards(incidents, limit=5):
    cards = []
    for item in incidents[:limit]:
        analysis = item.get("analysis") or {}
        cards.append(
            {
                "incident_id": item.get("incident_id", "n/a"),
                "alert_name": item.get("alert_name", "n/a"),
                "pod": item.get("pod", "n/a"),
                "root_cause": analysis.get("root_cause", "n/a"),
                "recommendation": analysis.get("recommendation", "n/a"),
                "decision_source": analysis.get("decision_source", "n/a"),
                "confidence": analysis.get("confidence", "n/a"),
            }
        )
    return cards


def calc_active_incidents(incidents, active_window_minutes):
    now = datetime.now(timezone.utc)
    active = []
    for item in incidents:
        created_at = to_datetime(item.get("created_at"))
        if created_at is None:
            continue
        delta_minutes = (now - created_at).total_seconds() / 60.0
        if delta_minutes <= active_window_minutes:
            active.append(item)
    return active


def remediation_outcome_summary(remediations):
    if not remediations:
        return {}
    counts = Counter(str(item.get("outcome", "unknown")).strip().lower() for item in remediations)
    return dict(sorted(counts.items(), key=lambda kv: kv[0]))


st.set_page_config(page_title="AIOps Operations Dashboard", page_icon="AI", layout="wide")
st.title("AIOps Operations Dashboard")
st.caption("Live visibility for incidents, RCA decisions, remediation execution, and RAG health")

with st.sidebar:
    st.header("Data Source")
    base_url = st.text_input("AI Engine Base URL", value=DEFAULT_BASE_URL).rstrip("/")
    incident_limit = st.slider("Incident fetch limit", min_value=10, max_value=200, value=50, step=10)
    remediation_limit = st.slider("Remediation fetch limit", min_value=20, max_value=500, value=100, step=20)
    active_window = st.slider("Active window (minutes)", min_value=5, max_value=180, value=30, step=5)
    auto_refresh = st.toggle("Auto refresh", value=True)
    refresh_interval = st.slider("Refresh interval (seconds)", min_value=5, max_value=60, value=15, step=5)

if auto_refresh:
    st.markdown(
        f"""
        <script>
            setTimeout(function() {{
                window.location.reload();
            }}, {refresh_interval * 1000});
        </script>
        """,
        unsafe_allow_html=True,
    )

incidents_payload, incidents_error = fetch_json(f"{base_url}/incidents?limit={incident_limit}")
rem_payload, rem_error = fetch_json(f"{base_url}/incidents/remediations?limit={remediation_limit}")
rag_payload, rag_error = fetch_json(f"{base_url}/diagnostics/rag")

if incidents_error or rem_error:
    if incidents_error:
        st.error(f"Incidents endpoint error: {incidents_error}")
    if rem_error:
        st.error(f"Remediation history endpoint error: {rem_error}")

incidents = (incidents_payload or {}).get("incidents", [])
remediations = (rem_payload or {}).get("remediation_history", [])
rag = rag_payload or {}
active_incidents = calc_active_incidents(incidents, active_window_minutes=active_window)
outcome_counts = remediation_outcome_summary(remediations)

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Incidents", len(incidents))
col2.metric("Active Incidents", len(active_incidents))
col3.metric("Remediation Attempts", len(remediations))
col4.metric("RAG Collection Count", rag.get("collection_count", "n/a"))

st.divider()

left, right = st.columns([2, 1])

with left:
    st.subheader("Recent Incidents")
    incidents_df = format_incidents_dataframe(incidents)
    if incidents_df.empty:
        st.info("No incidents available yet.")
    else:
        st.dataframe(incidents_df, use_container_width=True, hide_index=True)

with right:
    st.subheader("Remediation Outcomes")
    if not outcome_counts:
        st.info("No remediation outcomes available yet.")
    else:
        chart_df = pd.DataFrame(
            {"outcome": list(outcome_counts.keys()), "count": list(outcome_counts.values())}
        ).set_index("outcome")
        st.bar_chart(chart_df)

st.divider()

st.subheader("RCA Summary (Latest 5)")
rca_cards = latest_rca_cards(incidents, limit=5)
if not rca_cards:
    st.info("No RCA records available yet.")
else:
    for card in rca_cards:
        with st.container(border=True):
            st.markdown(
                "**Incident:** {incident} | **Alert:** {alert} | **Pod:** {pod}".format(
                    incident=card["incident_id"],
                    alert=card["alert_name"],
                    pod=card["pod"],
                )
            )
            st.write(f"Root Cause: {card['root_cause']}")
            st.write(f"Recommendation: {card['recommendation']}")
            st.write(
                "Decision Source: {source} | Confidence: {confidence}".format(
                    source=card["decision_source"],
                    confidence=card["confidence"],
                )
            )

st.divider()

st.subheader("Remediation History")
rem_df = format_remediations_dataframe(remediations)
if rem_df.empty:
    st.info("No remediation history yet.")
else:
    st.dataframe(rem_df, use_container_width=True, hide_index=True)

st.divider()

st.subheader("RAG Diagnostics")
if rag_error:
    st.warning(f"RAG diagnostics endpoint error: {rag_error}")
else:
    st.json(rag)
