from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

import hmac

from fastapi import Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models, services
from .auth import Principal, current_hub, issue_user_token, require_role, require_site_access
from .config import ROOT, get_settings, validate_production_settings
from .db import get_db, init_db
from .legacy_compat import router as legacy_router
from .schemas import (
    BootstrapRequest,
    BootstrapResponse,
    EvidenceUploadRequest,
    EvidenceUploadResponse,
    IncidentOut,
    LoginRequest,
    MeResponse,
    MfaEnrollResponse,
    MfaVerifyRequest,
    NoteCreate,
    NoteOut,
    OpportunityCreate,
    OpportunityOut,
    PasswordChangeRequest,
    PrivacyAuditOut,
    ReportCreate,
    ReportResponse,
    ResourceEventCreate,
    ResourceEventOut,
    RouteAlertCreate,
    RouteAlertOut,
    RouteStatusOut,
    RumorCreate,
    RumorClusterOut,
    SiteOut,
    StatusPatch,
    SyncBatchIn,
    SyncBatchOut,
    TimelineEventOut,
    TokenResponse,
)


settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_settings(settings)
    init_db()
    from services.api import peacepulse_core as legacy

    legacy.init_db(seed_demo_data=True)
    yield


app = FastAPI(title="PeacePulse Hub API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env != "production" else [],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["authorization", "content-type", "x-hub-id", "x-hub-signature"],
)
app.include_router(legacy_router)


@app.get("/api/v1/health")
def health(db: Annotated[Session, Depends(get_db)]) -> dict[str, object]:
    db.execute(select(1)).scalar_one()
    return {"ok": True, "service": "peacepulse-api", "database": "ok", "env": settings.env}


@app.post("/api/v1/admin/bootstrap", response_model=BootstrapResponse, status_code=201)
def bootstrap(
    payload: BootstrapRequest,
    db: Annotated[Session, Depends(get_db)],
    x_bootstrap_token: Annotated[str, Header(alias="X-Bootstrap-Token")] = "",
) -> dict[str, str]:
    if settings.env == "production" and not hmac.compare_digest(x_bootstrap_token, settings.bootstrap_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bootstrap token.")
    return services.bootstrap(db, payload)


@app.post("/api/v1/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Annotated[Session, Depends(get_db)]) -> TokenResponse:
    token = issue_user_token(db, payload.email, payload.password, payload.mfa_code)
    return TokenResponse(access_token=token, expires_in=settings.access_token_minutes * 60)


@app.get("/api/v1/auth/me", response_model=MeResponse)
def me(principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))]) -> MeResponse:
    return MeResponse(
        user_id=principal.user.id,
        email=principal.user.email,
        roles=sorted(principal.roles),
        organization_ids=sorted(principal.organization_ids),
        site_ids=sorted(principal.site_ids),
        mfa_enabled=principal.user.mfa_enabled,
    )


@app.post("/api/v1/auth/mfa/enroll", response_model=MfaEnrollResponse)
def enroll_mfa(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))],
) -> MfaEnrollResponse:
    from .security import make_totp_secret, protect_secret

    secret = make_totp_secret()
    principal.user.mfa_secret_hash = protect_secret(secret)
    principal.user.mfa_enabled = False
    services.audit(db, principal.primary_org_id, None, principal.user.id, "auth.mfa_enroll_started", "user", principal.user.id, "MFA enrollment started.")
    db.commit()
    return MfaEnrollResponse(
        secret=secret,
        otpauth_url=f"otpauth://totp/PeacePulse:{principal.user.email}?secret={secret}&issuer=PeacePulse",
    )


@app.post("/api/v1/auth/mfa/verify-enrollment")
def verify_mfa_enrollment(
    payload: MfaVerifyRequest,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))],
) -> dict[str, bool]:
    from .security import reveal_secret, verify_totp

    if not principal.user.mfa_secret_hash or not verify_totp(reveal_secret(principal.user.mfa_secret_hash), payload.code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid MFA code.")
    principal.user.mfa_enabled = True
    services.audit(db, principal.primary_org_id, None, principal.user.id, "auth.mfa_enrolled", "user", principal.user.id, "MFA enrollment verified.")
    db.commit()
    return {"mfa_enabled": True}


@app.post("/api/v1/auth/change-password")
def change_password(
    payload: PasswordChangeRequest,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))],
) -> dict[str, bool]:
    from .security import hash_password, verify_password

    if not verify_password(payload.current_password, principal.user.password_hash):
        services.audit(db, principal.primary_org_id, None, principal.user.id, "auth.password_change_failed", "user", principal.user.id, "Invalid current password.")
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect.")
    principal.user.password_hash = hash_password(payload.new_password)
    services.audit(db, principal.primary_org_id, None, principal.user.id, "auth.password_changed", "user", principal.user.id, "Password changed.")
    db.commit()
    return {"changed": True}


@app.post("/api/v1/auth/logout")
def logout(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))],
) -> dict[str, bool]:
    services.audit(db, principal.primary_org_id, None, principal.user.id, "auth.logout", "user", principal.user.id, "User signed out.")
    db.commit()
    return {"logged_out": True}


@app.get("/api/v1/public/sites", response_model=list[SiteOut])
def public_sites(db: Annotated[Session, Depends(get_db)]) -> list[models.Site]:
    return list(db.scalars(select(models.Site).where(models.Site.status == "active").order_by(models.Site.name)))


@app.post("/api/v1/public/sites/{site_id}/reports", response_model=ReportResponse, status_code=201)
def public_report(site_id: str, payload: ReportCreate, db: Annotated[Session, Depends(get_db)]) -> ReportResponse:
    site = db.get(models.Site, site_id)
    if not site or site.status != "active":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    report, incident = services.create_report(db, site, payload)
    return ReportResponse(
        id=report.id,
        incident_id=incident.id,
        category=incident.category,
        severity=incident.severity,
        redacted_text=incident.redacted_text,
        public_update=incident.public_update,
    )


@app.get("/api/v1/incidents", response_model=list[IncidentOut])
def list_incidents(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin", "system_admin"))],
    site_id: str | None = None,
) -> list[dict]:
    query = select(models.Incident).where(models.Incident.organization_id.in_(principal.organization_ids))
    if site_id:
        require_site_access(db, principal, site_id)
        query = query.where(models.Incident.site_id == site_id)
    elif principal.site_ids and "org_admin" not in principal.roles and "system_admin" not in principal.roles:
        query = query.where(models.Incident.site_id.in_(principal.site_ids))
    query = query.order_by(models.Incident.created_at.desc())
    return [services.incident_to_dict(item) for item in db.scalars(query)]


@app.patch("/api/v1/incidents/{incident_id}/status", response_model=IncidentOut)
def patch_incident_status(
    incident_id: str,
    payload: StatusPatch,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> dict:
    incident = db.get(models.Incident, incident_id)
    if not incident or incident.organization_id not in principal.organization_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found.")
    require_site_access(db, principal, incident.site_id)
    updated = services.update_incident_status(db, principal.user, incident, payload.status)
    return services.incident_to_dict(updated)


@app.post("/api/v1/incidents/{incident_id}/notes", response_model=NoteOut, status_code=201)
def add_incident_note(
    incident_id: str,
    payload: NoteCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> models.IncidentNote:
    incident = db.get(models.Incident, incident_id)
    if not incident or incident.organization_id not in principal.organization_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found.")
    require_site_access(db, principal, incident.site_id)
    return services.create_note(db, principal.user, incident, payload.note)


@app.get("/api/v1/incidents/{incident_id}/notes", response_model=list[NoteOut])
def list_incident_notes(
    incident_id: str,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> list[models.IncidentNote]:
    incident = db.get(models.Incident, incident_id)
    if not incident or incident.organization_id not in principal.organization_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found.")
    require_site_access(db, principal, incident.site_id)
    return list(db.scalars(select(models.IncidentNote).where(models.IncidentNote.incident_id == incident_id).order_by(models.IncidentNote.created_at.desc())))


@app.get("/api/v1/incidents/{incident_id}/timeline", response_model=list[TimelineEventOut])
def incident_timeline(
    incident_id: str,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> list[dict[str, object]]:
    incident = db.get(models.Incident, incident_id)
    if not incident or incident.organization_id not in principal.organization_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incident not found.")
    require_site_access(db, principal, incident.site_id)
    return services.incident_timeline(db, incident)


@app.post("/api/v1/evidence/uploads", response_model=EvidenceUploadResponse, status_code=201)
def create_evidence_upload(
    payload: EvidenceUploadRequest,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> EvidenceUploadResponse:
    site = require_site_access(db, principal, payload.site_id)
    record = services.create_evidence_upload(db, site.organization_id, payload)
    return EvidenceUploadResponse(
        id=record.id,
        object_key=record.object_key,
        upload_url=services.evidence_upload_url(record),
        headers={"x-amz-server-side-encryption": "AES256"},
    )


@app.get("/api/v1/evidence")
def list_evidence(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
    site_id: str | None = None,
) -> list[dict[str, object]]:
    query = select(models.EvidenceRecord).where(models.EvidenceRecord.organization_id.in_(principal.organization_ids))
    if site_id:
        require_site_access(db, principal, site_id)
        query = query.where(models.EvidenceRecord.site_id == site_id)
    records = db.scalars(query.order_by(models.EvidenceRecord.created_at.desc()))
    return [
        {
            "id": item.id,
            "site_id": item.site_id,
            "filename": item.filename,
            "mime_type": item.mime_type,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
            "object_key": item.object_key,
            "sync_allowed": item.sync_allowed,
        }
        for item in records
    ]


@app.post("/api/v1/resources/events", status_code=201)
def create_resource_event(
    payload: ResourceEventCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> dict[str, object]:
    site = require_site_access(db, principal, payload.site_id)
    event = services.create_resource_event(db, site.organization_id, payload)
    return {"id": event.id, "anomaly": event.anomaly}


@app.get("/api/v1/resources/status", response_model=list[ResourceEventOut])
def resource_status(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
    site_id: str | None = None,
) -> list[models.ResourceEvent]:
    if site_id:
        site = require_site_access(db, principal, site_id)
        return services.list_resource_status(db, site.organization_id, {site.id})
    site_ids = None if {"org_admin", "system_admin"}.intersection(principal.roles) else principal.site_ids
    return services.list_resource_status(db, principal.primary_org_id, site_ids)


@app.post("/api/v1/rumors", status_code=201)
def create_rumor(
    payload: RumorCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> dict[str, object]:
    site = require_site_access(db, principal, payload.site_id)
    rumor = services.create_rumor(db, site.organization_id, payload)
    return {"id": rumor.id, "severity": rumor.severity, "cluster_key": rumor.cluster_key, "redacted_text": rumor.redacted_text}


@app.get("/api/v1/rumors/clusters", response_model=list[RumorClusterOut])
def rumor_clusters(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
    site_id: str | None = None,
) -> list[dict[str, object]]:
    if site_id:
        site = require_site_access(db, principal, site_id)
        return services.list_rumor_clusters(db, site.organization_id, {site.id})
    site_ids = None if {"org_admin", "system_admin"}.intersection(principal.roles) else principal.site_ids
    return services.list_rumor_clusters(db, principal.primary_org_id, site_ids)


@app.get("/api/v1/routes/status", response_model=RouteStatusOut)
def route_status(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
    site_id: str | None = None,
) -> dict[str, object]:
    if site_id:
        site = require_site_access(db, principal, site_id)
        return services.list_route_status(db, site.organization_id, {site.id})
    site_ids = None if {"org_admin", "system_admin"}.intersection(principal.roles) else principal.site_ids
    return services.list_route_status(db, principal.primary_org_id, site_ids)


@app.post("/api/v1/routes/alerts", response_model=RouteAlertOut, status_code=201)
def create_route_alert(
    payload: RouteAlertCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> models.RouteAlert:
    site = require_site_access(db, principal, payload.site_id)
    return services.create_route_alert(db, site.organization_id, payload)


@app.get("/api/v1/work/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
    site_id: str | None = None,
) -> list[models.Opportunity]:
    if site_id:
        site = require_site_access(db, principal, site_id)
        return services.list_opportunities(db, site.organization_id, {site.id})
    site_ids = None if {"org_admin", "system_admin"}.intersection(principal.roles) else principal.site_ids
    return services.list_opportunities(db, principal.primary_org_id, site_ids)


@app.post("/api/v1/work/opportunities", response_model=OpportunityOut, status_code=201)
def create_opportunity(
    payload: OpportunityCreate,
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("steward", "coordinator", "org_admin"))],
) -> models.Opportunity:
    site = require_site_access(db, principal, payload.site_id)
    return services.create_opportunity(db, site.organization_id, payload)


@app.post("/api/v1/hubs/{hub_id}/sync/batches", response_model=SyncBatchOut)
def sync_batch(
    hub_id: str,
    payload: SyncBatchIn,
    hub: Annotated[models.HubDevice, Depends(current_hub)],
    db: Annotated[Session, Depends(get_db)],
) -> dict[str, object]:
    if hub.id != hub_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Hub mismatch.")
    return services.accept_sync_batch(db, hub, payload)


@app.get("/api/v1/privacy/audit", response_model=PrivacyAuditOut)
def privacy_audit(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("coordinator", "org_admin", "system_admin"))],
) -> dict[str, object]:
    return services.privacy_audit(db, principal.primary_org_id)


@app.get("/api/v1/audit-events")
def audit_events(
    db: Annotated[Session, Depends(get_db)],
    principal: Annotated[Principal, Depends(require_role("org_admin", "system_admin"))],
) -> list[dict[str, object]]:
    rows = db.scalars(
        select(models.AuditEvent)
        .where(models.AuditEvent.organization_id.in_(principal.organization_ids))
        .order_by(models.AuditEvent.created_at.desc())
        .limit(200)
    )
    return [
        {
            "id": item.id,
            "action": item.action,
            "subject_type": item.subject_type,
            "subject_id": item.subject_id,
            "detail": item.detail,
            "created_at": item.created_at,
        }
        for item in rows
    ]


# Compatibility endpoints let the existing PWA keep loading while production UI work catches up.
WEB_ROOT = ROOT / "apps" / "web"
if WEB_ROOT.exists():
    app.mount("/assets", StaticFiles(directory=WEB_ROOT), name="assets")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")


@app.get("/{path:path}")
def static_or_spa(path: str) -> FileResponse:
    target = (WEB_ROOT / path).resolve()
    web_root = WEB_ROOT.resolve()
    if target.exists() and target.is_file() and target.is_relative_to(web_root):
        return FileResponse(target)
    return FileResponse(WEB_ROOT / "index.html")
