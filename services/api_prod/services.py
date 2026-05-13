from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .config import get_settings
from .privacy import keywords, redact
from .schemas import (
    BootstrapRequest,
    EvidenceUploadRequest,
    OpportunityCreate,
    ReportCreate,
    ResourceEventCreate,
    RouteAlertCreate,
    RumorCreate,
    SyncBatchIn,
)
from .security import hash_password, hash_token, make_token_urlsafe, new_id


ALLOWED_LANGUAGES = {"en", "sw", "fr", "ar"}
REPORT_CATEGORIES = {
    "resource": ["water", "queue", "pump", "food", "stock", "clinic", "distribution", "solar"],
    "threat": ["threat", "attack", "intimidat", "violence", "weapon", "unsafe"],
    "corruption": ["bribe", "favor", "divert", "stolen", "corrupt", "abuse"],
    "service_denial": ["denied", "turned away", "refused", "blocked", "excluded"],
    "rumor": ["rumor", "heard", "people say", "claim", "spreading"],
    "unsafe_route": ["road", "route", "checkpoint", "blocked", "bridge"],
    "work_exploitation": ["work", "wage", "pay", "exploitation", "job"],
}
PUBLIC_UPDATE_TEMPLATES = {
    "en": "Community stewards are reviewing a {category} concern near {location}. Please use verified service points and avoid sharing identifying details.",
    "sw": "Wasimamizi wa jamii wanapitia suala la {category} karibu na {location}. Tafadhali tumia vituo vilivyothibitishwa na epuka kushiriki taarifa za kumtambulisha mtu.",
    "fr": "Les relais communautaires examinent une alerte {category} pres de {location}. Utilisez les points de service verifies et evitez les details identifiants.",
    "ar": "يراجع مشرفو المجتمع بلاغ {category} قرب {location}. استخدموا نقاط الخدمة المؤكدة وتجنبوا مشاركة التفاصيل التي تكشف الهوية.",
}
ALLOWED_EVIDENCE_MIME_PREFIXES = ("image/", "audio/", "text/", "application/pdf")
SERVICE_POINTS = [
    {"label": "North water point", "kind": "water", "rough_location": "North zone", "status": "open"},
    {"label": "Clinic route", "kind": "clinic", "rough_location": "East corridor", "status": "review"},
    {"label": "Support desk", "kind": "support", "rough_location": "Central market", "status": "open"},
    {"label": "Bridge path", "kind": "route", "rough_location": "South bridge", "status": "caution"},
]


def require(condition: bool, message: str, code: int = status.HTTP_400_BAD_REQUEST) -> None:
    if not condition:
        raise HTTPException(code, message)


def bootstrap(db: Session, data: BootstrapRequest) -> dict[str, str]:
    existing = db.scalar(select(func.count(models.Organization.id)))
    require(existing == 0, "System is already bootstrapped.", status.HTTP_409_CONFLICT)
    org = models.Organization(id=new_id("org"), name=data.organization_name.strip())
    site = models.Site(
        id=new_id("site"),
        organization_id=org.id,
        name=data.site_name.strip(),
        rough_location=data.site_rough_location.strip() or "unspecified",
    )
    user = models.User(
        id=new_id("usr"),
        email=data.admin_email.lower().strip(),
        password_hash=hash_password(data.admin_password),
        full_name=data.admin_name.strip(),
        mfa_enabled=False,
        mfa_secret_hash="",
    )
    memberships = [
        models.Membership(id=new_id("mem"), user_id=user.id, organization_id=org.id, site_id=None, role="org_admin"),
        models.Membership(id=new_id("mem"), user_id=user.id, organization_id=org.id, site_id=site.id, role="coordinator"),
    ]
    hub_secret = make_token_urlsafe()
    hub = models.HubDevice(
        id=new_id("hub"),
        organization_id=org.id,
        site_id=site.id,
        label=f"{site.name} edge hub",
        secret_hash=hash_token(hub_secret),
    )
    db.add_all([org, site, user, *memberships, hub])
    audit(db, org.id, site.id, user.id, "bootstrap", "organization", org.id, "Initial production tenant created.")
    db.commit()
    return {
        "organization_id": org.id,
        "site_id": site.id,
        "admin_user_id": user.id,
        "hub_id": hub.id,
        "hub_secret": hub_secret,
    }


def classify(text: str, hint: str = "") -> tuple[str, float]:
    lower = f"{hint} {text}".lower()
    scores = {category: sum(1 for keyword in terms if keyword in lower) for category, terms in REPORT_CATEGORIES.items()}
    if hint in REPORT_CATEGORIES:
        scores[hint] = scores.get(hint, 0) + 3
    category, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        return "other", 0.35
    return category, min(0.95, 0.45 + score * 0.18)


def severity_score(text: str, category: str) -> int:
    lower = text.lower()
    score = 2
    if category in {"threat", "service_denial", "corruption"}:
        score += 1
    for marker in ["violence", "weapon", "attack", "denied", "turned away", "tension", "diverted", "unsafe"]:
        if marker in lower:
            score += 1
    return max(1, min(5, score))


def cluster_key(category: str, location: str, key_terms: list[str]) -> str:
    anchor = "-".join(key_terms[:3]) or "general"
    location_key = re.sub(r"[^a-z0-9]+", "-", location.lower()).strip("-") or "unknown"
    return f"{category}:{location_key}:{anchor}"


def public_update(category: str, location: str, language: str) -> str:
    template = PUBLIC_UPDATE_TEMPLATES.get(language, PUBLIC_UPDATE_TEMPLATES["en"])
    return template.format(category=category.replace("_", " "), location=location)


def create_report(db: Session, site: models.Site, payload: ReportCreate) -> tuple[models.Report, models.Incident]:
    require(payload.language in ALLOWED_LANGUAGES, "Invalid language.")
    require(not payload.category_hint or payload.category_hint in REPORT_CATEGORIES, "Invalid concern type.")
    redacted = redact(payload.text)
    report = models.Report(
        id=new_id("rep"),
        organization_id=site.organization_id,
        site_id=site.id,
        language=payload.language,
        rough_location=payload.rough_location.strip() or "unspecified",
        category_hint=payload.category_hint,
        raw_text=payload.text,
        redacted_text=redacted,
    )
    category, confidence = classify(payload.text, payload.category_hint)
    terms = keywords(redacted)
    incident = models.Incident(
        id=new_id("inc"),
        report_id=report.id,
        organization_id=site.organization_id,
        site_id=site.id,
        category=category,
        severity=severity_score(payload.text, category),
        confidence=confidence,
        redacted_text=redacted,
        keywords_json=json.dumps(terms),
        cluster_key=cluster_key(category, report.rough_location, terms),
        public_update=public_update(category, report.rough_location, payload.language),
    )
    report.status = "triaged"
    report.raw_text = ""
    db.add_all([report, incident])
    audit(db, site.organization_id, site.id, None, "report.triaged", "report", report.id, "Raw text purged after triage.")
    db.commit()
    db.refresh(report)
    db.refresh(incident)
    return report, incident


def incident_to_dict(incident: models.Incident) -> dict[str, Any]:
    return {
        "id": incident.id,
        "site_id": incident.site_id,
        "category": incident.category,
        "severity": incident.severity,
        "confidence": incident.confidence,
        "redacted_text": incident.redacted_text,
        "keywords": json.loads(incident.keywords_json),
        "cluster_key": incident.cluster_key,
        "status": incident.status,
        "public_update": incident.public_update,
        "created_at": incident.created_at,
    }


def create_note(db: Session, user: models.User, incident: models.Incident, note: str) -> models.IncidentNote:
    record = models.IncidentNote(
        id=new_id("nte"),
        incident_id=incident.id,
        organization_id=incident.organization_id,
        site_id=incident.site_id,
        actor_user_id=user.id,
        note=redact(note),
    )
    db.add(record)
    audit(db, incident.organization_id, incident.site_id, user.id, "incident.note", "incident", incident.id, "Responder note added.")
    db.commit()
    db.refresh(record)
    return record


def update_incident_status(db: Session, user: models.User, incident: models.Incident, new_status: str) -> models.Incident:
    incident.status = new_status
    audit(db, incident.organization_id, incident.site_id, user.id, "incident.status", "incident", incident.id, new_status)
    db.commit()
    db.refresh(incident)
    return incident


def create_evidence_upload(db: Session, org_id: str, payload: EvidenceUploadRequest) -> models.EvidenceRecord:
    require(any(payload.mime_type.startswith(prefix) for prefix in ALLOWED_EVIDENCE_MIME_PREFIXES), "Evidence file type is not supported.")
    object_key = f"{org_id}/{payload.site_id}/{new_id('evd')}-{payload.filename}"
    record = models.EvidenceRecord(
        id=new_id("evd"),
        organization_id=org_id,
        site_id=payload.site_id,
        linked_report_id=payload.linked_report_id,
        filename=payload.filename,
        mime_type=payload.mime_type,
        sha256=payload.sha256.lower(),
        size_bytes=payload.size_bytes,
        object_key=object_key,
        sync_allowed=payload.sync_allowed,
    )
    db.add(record)
    audit(db, org_id, payload.site_id, None, "evidence.upload.created", "evidence", record.id, "Signed upload placeholder created.")
    db.commit()
    db.refresh(record)
    return record


def evidence_upload_url(record: models.EvidenceRecord) -> str:
    settings = get_settings()
    if settings.s3_endpoint_url:
        return f"{settings.s3_endpoint_url.rstrip('/')}/{settings.s3_bucket}/{record.object_key}"
    settings.evidence_storage_dir.mkdir(parents=True, exist_ok=True)
    return f"local://{record.object_key}"


def detect_anomaly(queue_length: int, flow_rate: float, uptime: int) -> str:
    flags = []
    if uptime == 0:
        flags.append("pump offline")
    if queue_length >= 40:
        flags.append("queue pressure")
    if flow_rate < 1.0:
        flags.append("low flow")
    return ", ".join(flags) if flags else "normal"


def create_resource_event(db: Session, org_id: str, payload: ResourceEventCreate) -> models.ResourceEvent:
    event = models.ResourceEvent(
        id=new_id("res"),
        organization_id=org_id,
        site_id=payload.site_id,
        resource_id=payload.resource_id,
        queue_length=payload.queue_length,
        flow_rate=payload.flow_rate,
        uptime=payload.uptime,
        maintenance_note=redact(payload.maintenance_note),
        anomaly=detect_anomaly(payload.queue_length, payload.flow_rate, payload.uptime),
    )
    db.add(event)
    if event.anomaly != "normal":
        audit(db, org_id, payload.site_id, None, "resource.anomaly", "resource_event", event.id, event.anomaly)
    db.commit()
    db.refresh(event)
    return event


def list_resource_status(db: Session, org_id: str, site_ids: set[str] | None = None) -> list[models.ResourceEvent]:
    query = select(models.ResourceEvent).where(models.ResourceEvent.organization_id == org_id)
    if site_ids:
        query = query.where(models.ResourceEvent.site_id.in_(site_ids))
    events = list(db.scalars(query.order_by(models.ResourceEvent.resource_id, models.ResourceEvent.created_at.desc())))
    latest: dict[str, models.ResourceEvent] = {}
    for event in events:
        key = f"{event.site_id}:{event.resource_id}"
        latest.setdefault(key, event)
    return list(latest.values())


def create_rumor(db: Session, org_id: str, payload: RumorCreate) -> models.Rumor:
    redacted = redact(payload.text)
    terms = keywords(redacted)
    rumor = models.Rumor(
        id=new_id("rum"),
        organization_id=org_id,
        site_id=payload.site_id,
        language=payload.language,
        rough_location=payload.rough_location,
        redacted_text=redacted,
        severity=severity_score(payload.text, "rumor"),
        cluster_key=cluster_key("rumor", payload.rough_location, terms),
        response_notes=redact(payload.response_notes),
    )
    db.add(rumor)
    audit(db, org_id, payload.site_id, None, "rumor.created", "rumor", rumor.id, rumor.cluster_key)
    db.commit()
    db.refresh(rumor)
    return rumor


def list_rumor_clusters(db: Session, org_id: str, site_ids: set[str] | None = None) -> list[dict[str, Any]]:
    query = select(models.Rumor).where(models.Rumor.organization_id == org_id)
    if site_ids:
        query = query.where(models.Rumor.site_id.in_(site_ids))
    rumors = list(db.scalars(query.order_by(models.Rumor.created_at.desc())))
    clusters: dict[str, dict[str, Any]] = {}
    for rumor in rumors:
        cluster = clusters.setdefault(
            rumor.cluster_key,
            {"cluster_key": rumor.cluster_key, "count": 0, "max_severity": 0, "latest_at": rumor.created_at, "items": []},
        )
        cluster["count"] += 1
        cluster["max_severity"] = max(cluster["max_severity"], rumor.severity)
        cluster["latest_at"] = max(cluster["latest_at"], rumor.created_at)
        cluster["items"].append(
            {
                "id": rumor.id,
                "created_at": rumor.created_at,
                "language": rumor.language,
                "rough_location": rumor.rough_location,
                "redacted_text": rumor.redacted_text,
                "severity": rumor.severity,
                "response_notes": rumor.response_notes,
            }
        )
    return sorted(clusters.values(), key=lambda item: (item["max_severity"], item["count"]), reverse=True)


def create_route_alert(db: Session, org_id: str, payload: RouteAlertCreate) -> models.RouteAlert:
    alert = models.RouteAlert(
        id=new_id("rte"),
        organization_id=org_id,
        site_id=payload.site_id,
        route_label=redact(payload.route_label),
        rough_location=payload.rough_location.strip() or "unspecified",
        alert_type=payload.alert_type,
        status=payload.status,
        note=redact(payload.note),
    )
    db.add(alert)
    audit(db, org_id, payload.site_id, None, "route_alert.created", "route_alert", alert.id, alert.status)
    db.commit()
    db.refresh(alert)
    return alert


def list_route_status(db: Session, org_id: str, site_ids: set[str] | None = None) -> dict[str, Any]:
    query = select(models.RouteAlert).where(models.RouteAlert.organization_id == org_id)
    if site_ids:
        query = query.where(models.RouteAlert.site_id.in_(site_ids))
    alerts = list(db.scalars(query.order_by(models.RouteAlert.created_at.desc())))
    return {"service_points": SERVICE_POINTS, "alerts": alerts}


def create_opportunity(db: Session, org_id: str, payload: OpportunityCreate) -> models.Opportunity:
    opportunity = models.Opportunity(
        id=new_id("opp"),
        organization_id=org_id,
        site_id=payload.site_id,
        title=redact(payload.title),
        skill_category=payload.skill_category,
        rough_location=payload.rough_location.strip() or "unspecified",
        verification_status=payload.verification_status,
        safety_note=redact(payload.safety_note),
    )
    db.add(opportunity)
    audit(db, org_id, payload.site_id, None, "opportunity.created", "opportunity", opportunity.id, opportunity.verification_status)
    db.commit()
    db.refresh(opportunity)
    return opportunity


def list_opportunities(db: Session, org_id: str, site_ids: set[str] | None = None) -> list[models.Opportunity]:
    query = select(models.Opportunity).where(models.Opportunity.organization_id == org_id)
    if site_ids:
        query = query.where(models.Opportunity.site_id.in_(site_ids))
    return list(db.scalars(query.order_by(models.Opportunity.created_at.desc(), models.Opportunity.id.desc())))


def incident_timeline(db: Session, incident: models.Incident) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [
        {
            "created_at": incident.created_at,
            "kind": "triage",
            "title": f"{incident.category.replace('_', ' ')} triaged",
            "detail": f"Severity {incident.severity} with {round(incident.confidence * 100)}% confidence.",
        }
    ]
    notes = db.scalars(select(models.IncidentNote).where(models.IncidentNote.incident_id == incident.id).order_by(models.IncidentNote.created_at.desc()))
    for note in notes:
        events.append({"created_at": note.created_at, "kind": "note", "title": "Responder note", "detail": note.note})
    evidence = db.scalars(select(models.EvidenceRecord).where(models.EvidenceRecord.linked_report_id == incident.report_id).order_by(models.EvidenceRecord.created_at.desc()))
    for item in evidence:
        events.append({"created_at": item.created_at, "kind": "evidence", "title": item.filename, "detail": f"SHA-256 {item.sha256[:16]}... metadata only."})
    resources = db.scalars(select(models.ResourceEvent).where(models.ResourceEvent.site_id == incident.site_id).order_by(models.ResourceEvent.created_at.desc()).limit(3))
    for item in resources:
        events.append({"created_at": item.created_at, "kind": "resource", "title": item.resource_id, "detail": f"{item.anomaly}; queue {item.queue_length}; flow {item.flow_rate}."})
    for cluster in list_rumor_clusters(db, incident.organization_id, {incident.site_id}):
        events.append({"created_at": cluster["latest_at"], "kind": "rumor", "title": f"{cluster['count']} related rumor report(s)", "detail": f"Max severity {cluster['max_severity']}; human review required."})
    return sorted(events, key=lambda item: (item["created_at"], item["kind"]), reverse=True)


def accept_sync_batch(db: Session, hub: models.HubDevice, payload: SyncBatchIn) -> dict[str, Any]:
    existing = db.scalar(
        select(models.SyncBatch).where(
            models.SyncBatch.hub_device_id == hub.id,
            models.SyncBatch.idempotency_key == payload.idempotency_key,
        )
    )
    if existing:
        return {"batch_id": existing.id, "accepted": existing.accepted, "rejected": existing.rejected, "results": []}
    results: list[dict[str, Any]] = []
    accepted = 0
    rejected = 0
    for item in payload.items:
        serialized = json.dumps(item.payload).lower()
        if "raw_text" in item.payload or "encrypted_path" in item.payload or "content_base64" in item.payload:
            rejected += 1
            results.append({"item_id": item.item_id, "status": "rejected", "reason": "payload contains local-only fields"})
            continue
        if any(marker in serialized for marker in ["+254 700", "@example", "block c-12"]):
            rejected += 1
            results.append({"item_id": item.item_id, "status": "rejected", "reason": "payload appears unredacted"})
            continue
        accepted += 1
        results.append({"item_id": item.item_id, "status": "accepted"})
    batch = models.SyncBatch(
        id=new_id("sbn"),
        hub_device_id=hub.id,
        organization_id=hub.organization_id,
        site_id=hub.site_id,
        idempotency_key=payload.idempotency_key,
        accepted=accepted,
        rejected=rejected,
    )
    db.add(batch)
    audit(db, hub.organization_id, hub.site_id, None, "sync.batch", "hub_device", hub.id, f"{accepted} accepted, {rejected} rejected")
    db.commit()
    return {"batch_id": batch.id, "accepted": accepted, "rejected": rejected, "results": results}


def audit(db: Session, org_id: str | None, site_id: str | None, actor_user_id: str | None, action: str, subject_type: str, subject_id: str, detail: str) -> None:
    db.add(
        models.AuditEvent(
            id=new_id("aud"),
            organization_id=org_id,
            site_id=site_id,
            actor_user_id=actor_user_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            detail=detail,
        )
    )


def privacy_audit(db: Session, org_id: str) -> dict[str, Any]:
    counts = {
        "sites": db.scalar(select(func.count(models.Site.id)).where(models.Site.organization_id == org_id)) or 0,
        "reports": db.scalar(select(func.count(models.Report.id)).where(models.Report.organization_id == org_id)) or 0,
        "incidents": db.scalar(select(func.count(models.Incident.id)).where(models.Incident.organization_id == org_id)) or 0,
        "evidence": db.scalar(select(func.count(models.EvidenceRecord.id)).where(models.EvidenceRecord.organization_id == org_id)) or 0,
        "sync_batches": db.scalar(select(func.count(models.SyncBatch.id)).where(models.SyncBatch.organization_id == org_id)) or 0,
    }
    return {
        "counts": counts,
        "local_only": ["Raw report text is purged after triage.", "Hub device secrets are stored only as hashes."],
        "syncs": ["Redacted summaries, metadata, and aggregate resource/rumor signals."],
        "never_syncs": ["Raw evidence bytes in JSON payloads.", "Local evidence paths.", "Unredacted report text."],
    }
