from __future__ import annotations

import hashlib
import hmac
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .domain import (
    ALLOWED_EVIDENCE_MIME_PREFIXES,
    ALLOWED_LANGUAGES,
    REPORT_CATEGORIES,
    SERVICE_POINTS,
    classify,
    cluster_key,
    detect_anomaly,
    public_update,
    severity_score,
)
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
from .security import hash_password, hash_token, make_token_urlsafe, new_id, now_utc, sign_hub_payload


S3_SERVER_SIDE_ENCRYPTION = "AES256"

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
    db.add(org)
    db.flush()
    db.add_all([site, user])
    db.flush()
    db.add_all([*memberships, hub])
    db.flush()
    audit(db, org.id, site.id, user.id, "bootstrap", "organization", org.id, "Initial production tenant created.")
    db.commit()
    return {
        "organization_id": org.id,
        "site_id": site.id,
        "admin_user_id": user.id,
        "hub_id": hub.id,
        "hub_secret": hub_secret,
    }


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
    db.add(report)
    db.flush()

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
    db.add(incident)
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
    object_key = f"{org_id}/{payload.site_id}/{new_id('evd')}-{_safe_filename(payload.filename)}"
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


def store_evidence_content(db: Session, record: models.EvidenceRecord, content: bytes, content_type: str) -> models.EvidenceRecord:
    require(record.retention_status == "active", "Evidence record is not active.", status.HTTP_409_CONFLICT)
    require(record.storage_status in {"pending", "stored"}, "Evidence record cannot accept content.", status.HTTP_409_CONFLICT)
    require(content_type.split(";")[0].strip().startswith(record.mime_type.split(";")[0].strip()), "Evidence content type does not match metadata.")
    require(len(content) == record.size_bytes, "Evidence content size does not match metadata.")
    digest = hashlib.sha256(content).hexdigest()
    require(digest == record.sha256, "Evidence content hash does not match metadata.")

    stripped = strip_evidence_metadata(record.mime_type, content)
    encrypted = _xor_evidence_bytes(stripped)
    target = _evidence_storage_path(record)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(encrypted)
    record.storage_status = "stored"
    record.stored_size_bytes = len(encrypted)
    record.stored_at = now_utc()
    audit(db, record.organization_id, record.site_id, None, "evidence.content.stored", "evidence", record.id, "Evidence bytes stored encrypted at local edge.")
    db.commit()
    db.refresh(record)
    return record


def evidence_content_exists(record: models.EvidenceRecord) -> bool:
    return _evidence_storage_path(record).exists()


def evidence_upload_url(record: models.EvidenceRecord) -> str:
    return evidence_upload_target(record)["upload_url"]


def evidence_upload_target(record: models.EvidenceRecord) -> dict[str, Any]:
    settings = get_settings()
    if _evidence_s3_enabled(settings):
        return {
            "upload_url": _presigned_s3_put_url(settings, record),
            "headers": {
                "content-type": record.mime_type,
                "x-amz-server-side-encryption": S3_SERVER_SIDE_ENCRYPTION,
            },
            "storage_mode": "s3",
        }
    settings.evidence_storage_dir.mkdir(parents=True, exist_ok=True)
    return {
        "upload_url": f"/api/v1/evidence/uploads/{record.id}/content",
        "headers": {"x-amz-server-side-encryption": S3_SERVER_SIDE_ENCRYPTION},
        "storage_mode": "local",
    }


def evidence_storage_mode() -> str:
    return "s3" if _evidence_s3_enabled(get_settings()) else "local"


def strip_evidence_metadata(mime_type: str, content: bytes) -> bytes:
    if mime_type == "image/jpeg":
        return _strip_jpeg_metadata(content)
    if mime_type == "image/png":
        return _strip_png_metadata(content)
    return content


def _safe_filename(filename: str) -> str:
    name = filename.replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")
    return name[:120] or "evidence.bin"


def _evidence_storage_path(record: models.EvidenceRecord):
    settings = get_settings()
    return settings.evidence_storage_dir / f"{record.object_key}.enc"


def _evidence_s3_enabled(settings: Any) -> bool:
    return all(
        (
            settings.s3_endpoint_url.strip(),
            settings.s3_bucket.strip(),
            settings.s3_region.strip(),
            settings.s3_access_key_id.strip(),
            settings.s3_secret_access_key.strip(),
        )
    )


def _presigned_s3_put_url(settings: Any, record: models.EvidenceRecord) -> str:
    parsed = urlsplit(settings.s3_endpoint_url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError("PEACEPULSE_S3_ENDPOINT_URL must include a scheme and host.")

    timestamp = now_utc()
    amz_date = timestamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = timestamp.strftime("%Y%m%d")
    scope = f"{date_stamp}/{settings.s3_region.strip()}/s3/aws4_request"
    credential = f"{settings.s3_access_key_id.strip()}/{scope}"
    object_key = quote(record.object_key, safe="/-_.~")

    if settings.s3_force_path_style:
        canonical_uri = _join_url_path(parsed.path, settings.s3_bucket, object_key)
        host = parsed.netloc
    else:
        host = f"{settings.s3_bucket}.{parsed.netloc}"
        canonical_uri = _join_url_path(parsed.path, object_key)

    query_params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": credential,
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(int(settings.s3_presign_expires_seconds or 900)),
        "X-Amz-SignedHeaders": "content-type;host;x-amz-server-side-encryption",
    }
    if settings.s3_session_token:
        query_params["X-Amz-Security-Token"] = settings.s3_session_token.strip()

    canonical_querystring = "&".join(
        f"{quote(key, safe='-_.~')}={quote(value, safe='-_.~')}"
        for key, value in sorted(query_params.items())
    )
    canonical_headers = (
        f"content-type:{record.mime_type.strip()}\n"
        f"host:{host}\n"
        f"x-amz-server-side-encryption:{S3_SERVER_SIDE_ENCRYPTION}\n"
    )
    canonical_request = "\n".join(
        [
            "PUT",
            canonical_uri,
            canonical_querystring,
            canonical_headers,
            "content-type;host;x-amz-server-side-encryption",
            "UNSIGNED-PAYLOAD",
        ]
    )
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _aws4_signing_key(settings.s3_secret_access_key.strip(), date_stamp, settings.s3_region.strip(), "s3")
    signature = hmac_sha256(signing_key, string_to_sign).hex()
    separator = "&" if canonical_querystring else ""
    scheme = parsed.scheme
    return f"{scheme}://{host}{canonical_uri}?{canonical_querystring}{separator}X-Amz-Signature={signature}"


def _join_url_path(*parts: str) -> str:
    segments = [segment.strip("/") for segment in parts if segment and segment.strip("/")]
    return "/" + "/".join(segments)


def _aws4_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    key = f"AWS4{secret_key}".encode("utf-8")
    for value in (date_stamp, region, service, "aws4_request"):
        key = hmac_sha256(key, value)
    return key


def hmac_sha256(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _xor_evidence_bytes(content: bytes) -> bytes:
    key = hashlib.sha256(get_settings().jwt_secret.encode("utf-8")).digest()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(content))


def _strip_jpeg_metadata(content: bytes) -> bytes:
    if not content.startswith(b"\xff\xd8"):
        return content
    output = bytearray(content[:2])
    index = 2
    while index + 4 <= len(content):
        if content[index] != 0xFF:
            output.extend(content[index:])
            break
        marker = content[index + 1]
        if marker in {0xDA, 0xD9}:
            output.extend(content[index:])
            break
        segment_length = int.from_bytes(content[index + 2 : index + 4], "big")
        end = index + 2 + segment_length
        if end > len(content):
            return content
        if marker not in {0xE1, 0xE2, 0xFE}:
            output.extend(content[index:end])
        index = end
    return bytes(output)


def _strip_png_metadata(content: bytes) -> bytes:
    signature = b"\x89PNG\r\n\x1a\n"
    if not content.startswith(signature):
        return content
    output = bytearray(signature)
    index = len(signature)
    while index + 12 <= len(content):
        length = int.from_bytes(content[index : index + 4], "big")
        chunk_type = content[index + 4 : index + 8]
        end = index + 12 + length
        if end > len(content):
            return content
        if chunk_type not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            output.extend(content[index:end])
        index = end
        if chunk_type == b"IEND":
            break
    return bytes(output)


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


def _sync_item(item_type: str, item_id: str, created_at: Any, payload: dict[str, Any], site_id: str | None = None) -> dict[str, Any]:
    return {
        "id": f"{item_type}_{item_id}",
        "created_at": created_at,
        "item_type": item_type,
        "item_id": item_id,
        "site_id": site_id,
        "status": "pending",
        "synced_at": None,
        "payload_keys": sorted(payload.keys()),
        "summary": payload,
    }


def _sync_candidates(db: Session, org_id: str, site_ids: set[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    previews: list[dict[str, Any]] = []

    incident_query = select(models.Incident).where(models.Incident.organization_id == org_id)
    if site_ids:
        incident_query = incident_query.where(models.Incident.site_id.in_(site_ids))
    for incident in db.scalars(incident_query.order_by(models.Incident.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "incident_summary",
                incident.id,
                incident.created_at,
                {
                    "category": incident.category,
                    "severity": incident.severity,
                    "cluster_key": incident.cluster_key,
                    "redacted_text": incident.redacted_text,
                },
                incident.site_id,
            )
        )

    note_query = select(models.IncidentNote).where(models.IncidentNote.organization_id == org_id)
    if site_ids:
        note_query = note_query.where(models.IncidentNote.site_id.in_(site_ids))
    for note in db.scalars(note_query.order_by(models.IncidentNote.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "incident_note",
                note.id,
                note.created_at,
                {
                    "incident_id": note.incident_id,
                    "actor_label": note.actor_user_id or "steward",
                    "note": note.note,
                },
                note.site_id,
            )
        )

    evidence_query = select(models.EvidenceRecord).where(models.EvidenceRecord.organization_id == org_id)
    if site_ids:
        evidence_query = evidence_query.where(models.EvidenceRecord.site_id.in_(site_ids))
    for item in db.scalars(evidence_query.where(models.EvidenceRecord.sync_allowed.is_(True)).order_by(models.EvidenceRecord.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "evidence_record",
                item.id,
                item.created_at,
                {
                    "filename": item.filename,
                    "mime_type": item.mime_type,
                    "sha256": item.sha256,
                    "size_bytes": item.size_bytes,
                    "object_key": item.object_key,
                },
                item.site_id,
            )
        )

    resource_query = select(models.ResourceEvent).where(models.ResourceEvent.organization_id == org_id)
    if site_ids:
        resource_query = resource_query.where(models.ResourceEvent.site_id.in_(site_ids))
    for item in db.scalars(resource_query.order_by(models.ResourceEvent.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "resource_anomaly",
                item.id,
                item.created_at,
                {
                    "resource_id": item.resource_id,
                    "anomaly": item.anomaly,
                    "queue_length": item.queue_length,
                    "uptime": item.uptime,
                },
                item.site_id,
            )
        )

    rumor_query = select(models.Rumor).where(models.Rumor.organization_id == org_id)
    if site_ids:
        rumor_query = rumor_query.where(models.Rumor.site_id.in_(site_ids))
    for item in db.scalars(rumor_query.order_by(models.Rumor.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "rumor_summary",
                item.id,
                item.created_at,
                {
                    "rough_location": item.rough_location,
                    "severity": item.severity,
                    "cluster_key": item.cluster_key,
                    "redacted_text": item.redacted_text,
                },
                item.site_id,
            )
        )

    route_query = select(models.RouteAlert).where(models.RouteAlert.organization_id == org_id)
    if site_ids:
        route_query = route_query.where(models.RouteAlert.site_id.in_(site_ids))
    for item in db.scalars(route_query.order_by(models.RouteAlert.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "route_alert",
                item.id,
                item.created_at,
                {
                    "route_label": item.route_label,
                    "rough_location": item.rough_location,
                    "alert_type": item.alert_type,
                    "status": item.status,
                    "note": item.note,
                },
                item.site_id,
            )
        )

    opportunity_query = select(models.Opportunity).where(models.Opportunity.organization_id == org_id)
    if site_ids:
        opportunity_query = opportunity_query.where(models.Opportunity.site_id.in_(site_ids))
    for item in db.scalars(opportunity_query.order_by(models.Opportunity.created_at.desc()).limit(limit)):
        previews.append(
            _sync_item(
                "opportunity_summary",
                item.id,
                item.created_at,
                {
                    "title": item.title,
                    "skill_category": item.skill_category,
                    "rough_location": item.rough_location,
                    "verification_status": item.verification_status,
                },
                item.site_id,
            )
        )

    return sorted(previews, key=lambda item: item["created_at"], reverse=True)[:limit]


def sync_preview(db: Session, org_id: str, site_ids: set[str] | None = None, limit: int = 20) -> list[dict[str, Any]]:
    _ensure_sync_records(db, org_id, site_ids)
    query = select(models.SyncRecord).where(models.SyncRecord.organization_id == org_id, models.SyncRecord.status.in_(("pending", "failed")))
    if site_ids:
        query = query.where((models.SyncRecord.site_id.is_(None)) | (models.SyncRecord.site_id.in_(site_ids)))
    rows = db.scalars(query.order_by(models.SyncRecord.created_at.desc()).limit(max(1, min(limit, 100))))
    return [_sync_record_out(row) for row in rows]


def sync_history(db: Session, org_id: str, site_ids: set[str] | None = None, limit: int = 30) -> list[dict[str, Any]]:
    _ensure_sync_records(db, org_id, site_ids)
    query = select(models.SyncRecord).where(models.SyncRecord.organization_id == org_id)
    if site_ids:
        query = query.where((models.SyncRecord.site_id.is_(None)) | (models.SyncRecord.site_id.in_(site_ids)))
    rows = db.scalars(query.order_by(models.SyncRecord.updated_at.desc()).limit(max(1, min(limit, 100))))
    return [_sync_record_out(row) for row in rows]


def sync_status(db: Session, org_id: str, site_ids: set[str] | None = None) -> dict[str, int]:
    _ensure_sync_records(db, org_id, site_ids)
    query = select(models.SyncRecord.status, func.count(models.SyncRecord.id)).where(models.SyncRecord.organization_id == org_id)
    if site_ids:
        query = query.where((models.SyncRecord.site_id.is_(None)) | (models.SyncRecord.site_id.in_(site_ids)))
    counts = {status: count for status, count in db.execute(query.group_by(models.SyncRecord.status)).all()}
    return {"pending": counts.get("pending", 0), "synced": counts.get("synced", 0), "failed": counts.get("failed", 0)}


def run_sync(db: Session, org_id: str, site_ids: set[str] | None = None) -> dict[str, Any]:
    _ensure_sync_records(db, org_id, site_ids)
    records = _sync_records_for_run(db, org_id, site_ids)
    remote = _remote_sync_destination()
    now = now_utc()
    if not records:
        mode = "remote" if remote else "local"
        detail = "No pending sync records were available."
        audit(db, org_id, None, None, "sync.run", "sync_record", org_id, detail)
        db.commit()
        return {"synced": 0, **sync_status(db, org_id, site_ids), "delivery_mode": mode, "delivery_state": "idle", "delivery_detail": detail}

    if remote:
        result = _push_remote_sync(db, org_id, records, remote)
        audit(db, org_id, None, None, "sync.remote_push", "sync_record", org_id, result["delivery_detail"])
        db.commit()
        return {
            "synced": result["synced"],
            **sync_status(db, org_id, site_ids),
            "delivery_mode": "remote",
            "delivery_state": result["delivery_state"],
            "delivery_detail": result["delivery_detail"],
            "remote_batch_id": result.get("batch_id", ""),
            "remote_endpoint": result.get("endpoint", ""),
        }

    synced = 0
    for record in records:
        payload = json.loads(record.payload_json)
        serialized = json.dumps(payload).lower()
        if _sync_payload_has_local_only_fields(payload) or _sync_payload_looks_unredacted(serialized):
            record.status = "failed"
            record.failure_reason = "payload failed privacy guard"
            continue
        record.status = "synced"
        record.synced_at = now
        record.failure_reason = ""
        synced += 1
    audit(db, org_id, None, None, "sync.local_run", "sync_record", org_id, f"{synced} records marked synced locally.")
    db.commit()
    return {
        "synced": synced,
        **sync_status(db, org_id, site_ids),
        "delivery_mode": "local",
        "delivery_state": "pushed",
        "delivery_detail": "Synced locally because no remote coordinator is configured.",
    }


def _remote_sync_destination() -> dict[str, Any] | None:
    settings = get_settings()
    fields = (settings.remote_sync_url.strip(), settings.remote_sync_hub_id.strip(), settings.remote_sync_hub_secret.strip())
    if any(fields) and not all(fields):
        raise RuntimeError(
            "PEACEPULSE_REMOTE_SYNC_URL, PEACEPULSE_REMOTE_SYNC_HUB_ID, and PEACEPULSE_REMOTE_SYNC_HUB_SECRET must be configured together."
        )
    if not all(fields):
        return None
    return {
        "base_url": fields[0].rstrip("/"),
        "hub_id": fields[1],
        "hub_secret": fields[2],
        "timeout": float(settings.remote_sync_timeout_seconds or 10.0),
    }


def _sync_records_for_run(db: Session, org_id: str, site_ids: set[str] | None = None) -> list[models.SyncRecord]:
    query = select(models.SyncRecord).where(models.SyncRecord.organization_id == org_id, models.SyncRecord.status.in_(("pending", "failed")))
    if site_ids:
        query = query.where((models.SyncRecord.site_id.is_(None)) | (models.SyncRecord.site_id.in_(site_ids)))
    return list(db.scalars(query.order_by(models.SyncRecord.created_at.desc(), models.SyncRecord.id.desc())))


def _sync_batch_payload(org_id: str, remote_hub_id: str, records: list[models.SyncRecord]) -> SyncBatchIn:
    items = [
        {"item_type": record.item_type, "item_id": record.item_id, "payload": json.loads(record.payload_json)}
        for record in records
    ]
    key_source = json.dumps({"org_id": org_id, "remote_hub_id": remote_hub_id, "items": items}, sort_keys=True, separators=(",", ":"), default=str)
    return SyncBatchIn(idempotency_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest(), items=items)


def _remote_sync_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, follow_redirects=False)


def _push_remote_sync(db: Session, org_id: str, records: list[models.SyncRecord], remote: dict[str, Any]) -> dict[str, Any]:
    batch = _sync_batch_payload(org_id, remote["hub_id"], records)
    body = json.dumps(batch.model_dump(mode="python"), separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")
    endpoint = f"{remote['base_url']}/api/v1/hubs/{remote['hub_id']}/sync/batches"
    headers = {
        "content-type": "application/json",
        "x-hub-id": remote["hub_id"],
        "x-hub-signature": sign_hub_payload(hash_token(remote["hub_secret"]), body),
    }

    try:
        with _remote_sync_client(remote["timeout"]) as client:
            response = client.post(endpoint, content=body, headers=headers)
    except httpx.HTTPError as exc:
        return {
            "synced": 0,
            "delivery_state": "failed",
            "delivery_detail": f"Remote push failed: {exc}",
            "endpoint": endpoint,
        }

    if response.status_code >= 400:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        detail = payload.get("detail") or payload.get("error") or response.text or "Remote sync rejected the batch."
        return {
            "synced": 0,
            "delivery_state": "failed",
            "delivery_detail": f"Remote push failed: {detail}",
            "endpoint": endpoint,
        }

    try:
        payload = response.json()
    except ValueError as exc:
        return {
            "synced": 0,
            "delivery_state": "failed",
            "delivery_detail": f"Remote push failed: invalid JSON response from {endpoint}.",
            "endpoint": endpoint,
        }

    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(records):
        return {
            "synced": 0,
            "delivery_state": "failed",
            "delivery_detail": f"Remote push failed: incomplete batch response from {endpoint}.",
            "endpoint": endpoint,
        }

    record_lookup = {(record.item_type, record.item_id): record for record in records}
    accepted = 0
    rejected = 0
    now = now_utc()
    for record, result in zip(records, results):
        local_record = record_lookup.get((record.item_type, record.item_id))
        if not local_record:
            continue
        status_value = result.get("status")
        if status_value == "accepted":
            local_record.status = "synced"
            local_record.synced_at = now
            local_record.failure_reason = ""
            accepted += 1
            continue
        if status_value == "rejected":
            local_record.status = "failed"
            local_record.failure_reason = result.get("reason", "Remote sync rejected the payload.")
            rejected += 1
            continue
        return {
            "synced": 0,
            "delivery_state": "failed",
            "delivery_detail": f"Remote push failed: unexpected item status in response from {endpoint}.",
            "endpoint": endpoint,
        }

    return {
        "synced": accepted,
        "accepted": accepted,
        "rejected": rejected,
        "delivery_state": "pushed",
        "delivery_detail": f"Remote push to {endpoint} completed with {accepted} accepted and {rejected} rejected.",
        "batch_id": payload.get("batch_id", ""),
        "endpoint": endpoint,
    }


def _ensure_sync_records(db: Session, org_id: str, site_ids: set[str] | None = None) -> None:
    existing = {
        (row.item_type, row.item_id): row
        for row in db.scalars(select(models.SyncRecord).where(models.SyncRecord.organization_id == org_id))
    }
    changed = False
    for item in _sync_candidates(db, org_id, site_ids):
        key = (item["item_type"], item["item_id"])
        payload_json = json.dumps(item["summary"], sort_keys=True, default=str)
        payload_keys_json = json.dumps(item["payload_keys"])
        record = existing.get(key)
        if record:
            if record.status != "synced" and (record.payload_json != payload_json or record.payload_keys_json != payload_keys_json):
                record.payload_json = payload_json
                record.payload_keys_json = payload_keys_json
                record.status = "pending"
                record.failure_reason = ""
                changed = True
            continue
        db.add(
            models.SyncRecord(
                id=item["id"],
                organization_id=org_id,
                site_id=item["site_id"],
                item_type=item["item_type"],
                item_id=item["item_id"],
                payload_json=payload_json,
                payload_keys_json=payload_keys_json,
            )
        )
        changed = True
    if changed:
        db.commit()


def _sync_record_out(record: models.SyncRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "item_type": record.item_type,
        "item_id": record.item_id,
        "status": record.status,
        "synced_at": record.synced_at,
        "failure_reason": record.failure_reason,
        "payload_keys": json.loads(record.payload_keys_json),
        "summary": json.loads(record.payload_json),
    }


def _sync_payload_has_local_only_fields(payload: dict[str, Any]) -> bool:
    return "raw_text" in payload or "encrypted_path" in payload or "content_base64" in payload


def _sync_payload_looks_unredacted(serialized: str) -> bool:
    return any(marker in serialized for marker in ["+254 700", "@example", "block c-12"])



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
        if _sync_payload_has_local_only_fields(item.payload):
            rejected += 1
            results.append({"item_id": item.item_id, "status": "rejected", "reason": "payload contains local-only fields"})
            continue
        if _sync_payload_looks_unredacted(serialized):
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
        "copilot_runbooks": (
            db.scalar(
                select(func.count(models.CopilotRunbook.id)).where(
                    (models.CopilotRunbook.organization_id.is_(None)) | (models.CopilotRunbook.organization_id == org_id)
                )
            )
            or 0
        ),
        "copilot_sessions": db.scalar(select(func.count(models.CopilotSession.id)).where(models.CopilotSession.organization_id == org_id)) or 0,
        "copilot_messages": db.scalar(select(func.count(models.CopilotMessage.id)).where(models.CopilotMessage.organization_id == org_id)) or 0,
        "sync_batches": db.scalar(select(func.count(models.SyncBatch.id)).where(models.SyncBatch.organization_id == org_id)) or 0,
    }
    return {
        "counts": counts,
        "local_only": [
            "Raw report text is purged after triage.",
            "Hub device secrets are stored only as hashes.",
            "Copilot sessions stay on the local hub and use redacted incident context plus runbook citations.",
        ],
        "syncs": ["Redacted summaries, metadata, and aggregate resource/rumor signals."],
        "never_syncs": ["Raw evidence bytes in JSON payloads.", "Local evidence paths.", "Unredacted report text.", "Copilot chat transcripts."],
    }
