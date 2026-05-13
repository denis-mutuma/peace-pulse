from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Role = Literal["community", "steward", "coordinator", "org_admin", "system_admin"]


class BootstrapRequest(BaseModel):
    organization_name: str = Field(min_length=2, max_length=160)
    site_name: str = Field(min_length=2, max_length=160)
    site_rough_location: str = Field(default="unspecified", max_length=160)
    admin_email: str = Field(min_length=5, max_length=320)
    admin_password: str = Field(min_length=12, max_length=128)
    admin_name: str = Field(default="", max_length=160)


class BootstrapResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organization_id: str
    site_id: str
    admin_user_id: str
    hub_id: str
    hub_secret: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str
    mfa_code: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class SiteOut(BaseModel):
    id: str
    organization_id: str
    name: str
    rough_location: str


class MeResponse(BaseModel):
    user_id: str
    email: str
    roles: list[str]
    organization_ids: list[str]
    site_ids: list[str]
    mfa_enabled: bool


class MfaEnrollResponse(BaseModel):
    secret: str
    otpauth_url: str


class MfaVerifyRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)


class ReportCreate(BaseModel):
    language: str = Field(default="en", max_length=12)
    rough_location: str = Field(default="unspecified", max_length=160)
    category_hint: str = Field(default="", max_length=40)
    text: str = Field(min_length=8, max_length=2000)


class ReportResponse(BaseModel):
    id: str
    incident_id: str
    category: str
    severity: int
    redacted_text: str
    public_update: str


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    category: str
    severity: int
    confidence: float
    redacted_text: str
    keywords: list[str]
    cluster_key: str
    status: str
    public_update: str
    created_at: datetime


class StatusPatch(BaseModel):
    status: Literal["new", "assigned", "in_progress", "resolved"]


class NoteCreate(BaseModel):
    note: str = Field(min_length=4, max_length=500)


class NoteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    incident_id: str
    note: str
    created_at: datetime


class EvidenceUploadRequest(BaseModel):
    site_id: str
    filename: str = Field(min_length=1, max_length=160)
    mime_type: str = Field(min_length=3, max_length=160)
    size_bytes: int = Field(gt=0, le=20_000_000)
    sha256: str = Field(min_length=64, max_length=64)
    linked_report_id: str | None = None
    sync_allowed: bool = False


class EvidenceUploadResponse(BaseModel):
    id: str
    object_key: str
    upload_url: str
    headers: dict[str, str]


class ResourceEventCreate(BaseModel):
    site_id: str
    resource_id: str = Field(default="water-point-north", max_length=120)
    queue_length: int = Field(default=0, ge=0, le=100000)
    flow_rate: float = Field(default=0, ge=0)
    uptime: int = Field(default=1, ge=0, le=1)
    maintenance_note: str = Field(default="", max_length=240)


class ResourceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    resource_id: str
    queue_length: int
    flow_rate: float
    uptime: int
    maintenance_note: str
    anomaly: str
    created_at: datetime


class RumorCreate(BaseModel):
    site_id: str
    language: str = Field(default="en", max_length=12)
    rough_location: str = Field(default="unspecified", max_length=160)
    text: str = Field(min_length=8, max_length=2000)
    response_notes: str = Field(default="", max_length=240)


class RumorItemOut(BaseModel):
    id: str
    created_at: datetime
    language: str
    rough_location: str
    redacted_text: str
    severity: int
    response_notes: str


class RumorClusterOut(BaseModel):
    cluster_key: str
    count: int
    max_severity: int
    latest_at: datetime
    items: list[RumorItemOut]


class RouteAlertCreate(BaseModel):
    site_id: str
    route_label: str = Field(min_length=3, max_length=120)
    rough_location: str = Field(default="unspecified", max_length=160)
    alert_type: Literal["blocked", "caution", "service_update"] = "caution"
    status: Literal["open", "caution", "blocked", "review"] = "review"
    note: str = Field(default="", max_length=240)


class RouteAlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    route_label: str
    rough_location: str
    alert_type: str
    status: str
    note: str
    created_at: datetime


class RouteStatusOut(BaseModel):
    service_points: list[dict[str, str]]
    alerts: list[RouteAlertOut]


class OpportunityCreate(BaseModel):
    site_id: str
    title: str = Field(min_length=4, max_length=120)
    skill_category: Literal["water", "solar", "translation", "repair", "care", "logistics"]
    rough_location: str = Field(default="unspecified", max_length=160)
    verification_status: Literal["unverified", "steward_checked", "paused"] = "unverified"
    safety_note: str = Field(default="", max_length=240)


class OpportunityOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    site_id: str
    title: str
    skill_category: str
    rough_location: str
    verification_status: str
    safety_note: str
    created_at: datetime


class CopilotRunbookCreate(BaseModel):
    title: str = Field(min_length=4, max_length=160)
    category: str = Field(default="operations", max_length=60)
    content: str = Field(min_length=20, max_length=6000)
    tags: list[str] = Field(default_factory=list, max_length=12)
    source: str = Field(default="manual", max_length=80)


class CopilotRunbookOut(BaseModel):
    id: str
    title: str
    category: str
    source: str
    tags: list[str]
    excerpt: str
    created_at: datetime


class CopilotCitation(BaseModel):
    document_id: str
    title: str
    category: str
    score: float
    excerpt: str


class CopilotInvestigationOut(BaseModel):
    incident_id: str
    summary: str
    hypotheses: list[str]
    recommended_actions: list[str]
    verification: dict[str, Any]
    citations: list[CopilotCitation]
    agent_trace: list[str]


class CopilotSessionCreate(BaseModel):
    incident_id: str | None = None
    title: str = Field(default="PeacePulse copilot session", max_length=160)


class CopilotChatMessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=2000)


class CopilotMessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[CopilotCitation]
    action_payload: dict[str, Any]
    created_at: datetime


class CopilotSessionOut(BaseModel):
    id: str
    incident_id: str | None
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    messages: list[CopilotMessageOut] = Field(default_factory=list)


class TimelineEventOut(BaseModel):
    created_at: datetime
    kind: str
    title: str
    detail: str


class SyncItem(BaseModel):
    item_type: Literal["incident_summary", "evidence_record", "resource_anomaly", "rumor_summary", "route_alert", "opportunity_summary", "incident_note"]
    item_id: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]


class SyncBatchIn(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=120)
    items: list[SyncItem] = Field(default_factory=list, max_length=500)


class SyncBatchOut(BaseModel):
    batch_id: str
    accepted: int
    rejected: int
    results: list[dict[str, Any]]


class PrivacyAuditOut(BaseModel):
    counts: dict[str, int]
    local_only: list[str]
    syncs: list[str]
    never_syncs: list[str]
