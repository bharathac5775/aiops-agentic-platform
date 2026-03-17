from fastapi import FastAPI, Request
from datetime import datetime, timezone
from workflows.cpu_workflow import build_graph

workflow = build_graph()

app = FastAPI()


def log(level: str, message: str):
    timestamp = datetime.now(timezone.utc).isoformat()
    print(f"[{timestamp}] [{level}] {message}")


@app.get("/")
def health():
    return {"status": "AI Engine running"}


@app.post("/alerts")
async def receive_alert(request: Request):

    payload = await request.json()

    if not isinstance(payload, dict):
        log("IGNORED", "Invalid payload type")
        return {"message": "Invalid payload", "processed": 0, "ignored": 0, "failed": 0}

    alerts = payload.get("alerts", [])

    if not isinstance(alerts, list):
        log("IGNORED", "Invalid alerts format")
        return {"message": "Invalid alerts format", "processed": 0, "ignored": 0, "failed": 0}

    log("RECEIVED", f"{len(alerts)} alert(s) from Alertmanager")

    processed = 0
    ignored = 0
    failed = 0

    for alert in alerts:
        status = alert.get("status")
        labels = alert.get("labels") or {}
        alert_name = labels.get("alertname", "unknown")
        pod_name = labels.get("pod", "unknown")

        if status != "firing":
            log("IGNORED", f"{alert_name} on pod {pod_name} (status={status})")
            ignored += 1
            continue

        log("PROCESSING", f"{alert_name} on pod {pod_name}")

        try:
            state = workflow.invoke({
                "alert": alert
            })

            result = state.get("result", {})

            log("RESULT", f"{result}")
            processed += 1

        except Exception as error:
            log("FAILED", f"{alert_name} on pod {pod_name}: {error}")
            failed += 1

    log("SUMMARY", f"processed={processed} ignored={ignored} failed={failed}")

    return {
        "message": "Alert received",
        "processed": processed,
        "ignored": ignored,
        "failed": failed
    }


#  Analyze API
@app.post("/analyze")
async def analyze(request: Request):

    data = await request.json()

    alert = data.get("alert")

    log("ANALYZE", "Running RCA workflow")

    try:
        state = workflow.invoke({
            "alert": alert
        })

        result = state.get("result", {})

        return {
            "analysis": result
        }

    except Exception as e:
        log("ERROR", f"Analyze failed: {e}")
        return {"error": str(e)}


# Remediation API
@app.post("/remediate")
async def remediate(request: Request):

    data = await request.json()
    decision = data.get("decision")

    log("REMEDIATE", f"Executing action: {decision}")

    # Placeholder → actual K8s logic comes Day 14
    return {
        "status": "executed",
        "action": decision
    }