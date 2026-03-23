import os
import requests


def _is_enabled() -> bool:
    return os.getenv("DISCORD_NOTIFICATIONS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _fmt(value, default: str = "n/a") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def build_discord_message(report: dict) -> str:
    analysis = report.get("analysis") or {}
    decision = report.get("decision") or {}
    attempts = report.get("remediation_attempts") or []
    latest_attempt = attempts[-1] if attempts else {}

    lines = [
        "🚨 AIOps Alert",
        "",
        f"Incident: {_fmt(report.get('incident_id'))}",
        f"Alert: {_fmt(report.get('alert_name'))}",
        f"Namespace: {_fmt(report.get('namespace'))}",
        f"Pod: {_fmt(report.get('pod'))}",
        "",
        f"Root Cause: {_fmt(analysis.get('root_cause'))}",
        f"Action: {_fmt(analysis.get('recommendation'))}",
        f"Confidence: {_fmt(analysis.get('confidence'))}",
        "",
        f"Remediation: {_fmt(latest_attempt.get('outcome', decision.get('reason')))}",
    ]

    reason = _fmt(latest_attempt.get("reason"), default="")
    if reason:
        lines.append(f"Policy: {reason}")

    return "\n".join(lines)


def send_discord_alert(message: str) -> bool:
    if not _is_enabled():
        print("[NOTIFY] Discord notification disabled")
        return False

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook_url:
        print("[NOTIFY] No Discord webhook configured")
        return False

    payload = {"content": message[:1900]}

    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        if response.status_code not in (200, 204):
            print(f"[NOTIFY ERROR] status={response.status_code} body={response.text}")
            return False
        print("[NOTIFY] Alert sent to Discord")
        return True
    except Exception as error:
        print(f"[NOTIFY EXCEPTION] {error}")
        return False


def notify_discord_from_report(report: dict) -> bool:
    message = build_discord_message(report)
    return send_discord_alert(message)
