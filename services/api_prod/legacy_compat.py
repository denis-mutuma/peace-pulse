from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.api import peacepulse_core as legacy


router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return legacy.health_status()


@router.get("/api/privacy/audit")
def privacy_audit() -> dict:
    return legacy.privacy_audit()


@router.get("/api/incidents")
def list_incidents() -> list[dict]:
    return legacy.list_incidents()


@router.get("/api/incidents/{incident_id}/timeline")
def incident_timeline(incident_id: str) -> list[dict]:
    return legacy.incident_timeline(incident_id)


@router.get("/api/incidents/{incident_id}/notes")
def list_notes(incident_id: str) -> list[dict]:
    return legacy.list_incident_notes(incident_id)


@router.post("/api/incidents/{incident_id}/notes", status_code=201)
def create_note(incident_id: str, body: dict) -> dict:
    try:
        return legacy.create_incident_note(incident_id, body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/incidents/{incident_id}/history")
def status_history(incident_id: str) -> list[dict]:
    return legacy.list_status_history(incident_id)


@router.patch("/api/incidents/{incident_id}/status")
def update_status(incident_id: str, body: dict) -> dict:
    try:
        return legacy.update_incident_status(incident_id, body.get("status", ""), body.get("actor_label", "responder"))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/reports", status_code=201)
def create_report(body: dict) -> dict:
    try:
        report = legacy.create_report(body)
        incident = legacy.triage_report(report["id"])
        return {"report": legacy.public_report(report), "incident": incident}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/evidence")
def list_evidence() -> list[dict]:
    return legacy.list_evidence()


@router.post("/api/evidence", status_code=201)
def create_evidence(body: dict) -> dict:
    try:
        return legacy.create_evidence(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/resources/status")
def resource_status() -> list[dict]:
    return legacy.resource_status()


@router.post("/api/sensor-events", status_code=201)
def create_resource_event(body: dict) -> dict:
    try:
        return legacy.create_resource_event(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/routes/status")
def route_status() -> dict:
    return legacy.route_status()


@router.post("/api/routes/alerts", status_code=201)
def create_route_alert(body: dict) -> dict:
    try:
        return legacy.create_route_alert(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/work/opportunities")
def list_opportunities() -> list[dict]:
    return legacy.list_opportunities()


@router.post("/api/work/opportunities", status_code=201)
def create_opportunity(body: dict) -> dict:
    try:
        return legacy.create_opportunity(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/rumors/clusters")
def list_rumors() -> list[dict]:
    return legacy.list_rumor_clusters()


@router.post("/api/rumors", status_code=201)
def create_rumor(body: dict) -> dict:
    try:
        return legacy.create_rumor(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/sync/status")
def sync_status() -> dict:
    return legacy.sync_status()


@router.get("/api/sync/preview")
def sync_preview() -> list[dict]:
    return legacy.sync_preview()


@router.post("/api/sync/run")
def run_sync() -> dict:
    return legacy.run_sync()


@router.post("/api/demo/reset")
def reset_demo() -> dict:
    return legacy.reset_demo_data()
