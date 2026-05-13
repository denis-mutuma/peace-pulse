from __future__ import annotations

import json
import re
import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .privacy import keywords, redact
from .security import new_id, now_utc


SEED_RUNBOOKS = [
    {
        "id": "rb_resource_pressure",
        "title": "Resource Pressure Response",
        "category": "resource",
        "tags": ["water", "queue", "resource", "mediation"],
        "content": (
            "When queue pressure or low resource flow appears, publish a non-identifying public update, "
            "ask stewards to verify service-point status, separate urgent safety concerns from routine "
            "maintenance, and create a short mediation note. Do not collect names, phone numbers, exact "
            "shelters, or identity documents."
        ),
    },
    {
        "id": "rb_rumor_control",
        "title": "RumorShield Verification",
        "category": "rumor",
        "tags": ["rumor", "verification", "public update"],
        "content": (
            "For repeated rumors, compare rough location, claim theme, severity, and trusted service-desk "
            "updates. Respond with a calm verified update, avoid naming alleged actors, and route threats "
            "or retaliation risks to responders before public broadcast."
        ),
    },
    {
        "id": "rb_route_safety",
        "title": "Route And Service Safety",
        "category": "unsafe_route",
        "tags": ["route", "clinic", "blocked", "caution"],
        "content": (
            "For route or service alerts, keep movement details coarse. Mark status as review, caution, "
            "blocked, or open. Ask stewards to confirm the service point, publish alternatives only at "
            "rough-location level, and never store exact movement trails."
        ),
    },
    {
        "id": "rb_fairwork_safety",
        "title": "FairWork Listing Review",
        "category": "work_exploitation",
        "tags": ["work", "opportunity", "exploitation", "safety"],
        "content": (
            "FairWork entries should be short, role-based, and steward reviewed. Do not store employer "
            "identity claims, worker profiles, payment details, or documents. Escalate exploitation "
            "concerns through anonymous reporting instead of naming a person in the listing."
        ),
    },
    {
        "id": "rb_evidence_metadata",
        "title": "Evidence Metadata Boundary",
        "category": "evidence",
        "tags": ["evidence", "metadata", "sync", "privacy"],
        "content": (
            "Evidence sync should include metadata, hashes, object keys, and consent flags only. Raw bytes, "
            "local evidence paths, unredacted report text, names, phone numbers, and exact shelters must "
            "stay out of coordinator sync payloads."
        ),
    },
]


@dataclass
class RetrievalHit:
    document_id: str
    title: str
    category: str
    score: float
    excerpt: str
    retrieval_method: str = "local_tfidf_cosine"


def ensure_seed_runbooks(db: Session, org_id: str | None = None) -> None:
    existing_rows = {row.id: row for row in db.scalars(select(models.CopilotRunbook).where(models.CopilotRunbook.organization_id.is_(None)))}
    changed = False
    for item in SEED_RUNBOOKS:
        existing = existing_rows.get(item["id"])
        if existing:
            if not existing.embedding_json or not existing.content_hash:
                existing.embedding_json = json.dumps(_vectorize(_runbook_text(existing.title, existing.category, json.loads(existing.tags_json), existing.content)))
                existing.content_hash = _content_hash(existing.content)
                changed = True
            continue
        db.add(
            models.CopilotRunbook(
                id=item["id"],
                organization_id=None,
                title=item["title"],
                category=item["category"],
                content=item["content"],
                tags_json=json.dumps(item["tags"]),
                source="seed",
                embedding_json=json.dumps(_vectorize(_runbook_text(item["title"], item["category"], item["tags"], item["content"]))),
                content_hash=_content_hash(item["content"]),
            )
        )
        changed = True
    if changed:
        db.commit()


def list_runbooks(db: Session, org_id: str) -> list[dict[str, Any]]:
    ensure_seed_runbooks(db)
    rows = db.scalars(
        select(models.CopilotRunbook)
        .where((models.CopilotRunbook.organization_id.is_(None)) | (models.CopilotRunbook.organization_id == org_id))
        .order_by(models.CopilotRunbook.category, models.CopilotRunbook.title)
    )
    return [_runbook_out(row) for row in rows]


def create_runbook(db: Session, org_id: str, payload: Any) -> dict[str, Any]:
    tags = [tag.strip().lower()[:40] for tag in payload.tags if tag.strip()]
    content = redact(payload.content.strip())
    category = payload.category.strip().lower() or "operations"
    title = payload.title.strip()
    runbook = models.CopilotRunbook(
        id=new_id("rb"),
        organization_id=org_id,
        title=title,
        category=category,
        content=content,
        tags_json=json.dumps(tags),
        source=payload.source.strip() or "manual",
        embedding_json=json.dumps(_vectorize(_runbook_text(title, category, tags, content))),
        content_hash=_content_hash(content),
    )
    db.add(runbook)
    db.commit()
    db.refresh(runbook)
    return _runbook_out(runbook)


def update_runbook(db: Session, org_id: str, runbook_id: str, payload: Any) -> dict[str, Any]:
    runbook = db.get(models.CopilotRunbook, runbook_id)
    if not runbook or runbook.organization_id != org_id:
        raise ValueError("Editable runbook not found.")
    if payload.title is not None:
        runbook.title = payload.title.strip()
    if payload.category is not None:
        runbook.category = payload.category.strip().lower() or "operations"
    if payload.content is not None:
        runbook.content = redact(payload.content.strip())
    if payload.tags is not None:
        runbook.tags_json = json.dumps([tag.strip().lower()[:40] for tag in payload.tags if tag.strip()])
    if payload.source is not None:
        runbook.source = payload.source.strip() or "manual"
    tags = json.loads(runbook.tags_json)
    runbook.embedding_json = json.dumps(_vectorize(_runbook_text(runbook.title, runbook.category, tags, runbook.content)))
    runbook.content_hash = _content_hash(runbook.content)
    db.commit()
    db.refresh(runbook)
    return _runbook_out(runbook)


def investigate_incident(db: Session, incident: models.Incident) -> dict[str, Any]:
    ensure_seed_runbooks(db)
    hits = retrieve_context(db, incident.organization_id, _incident_query(incident), limit=4)
    hypotheses = _hypotheses(incident, hits)
    actions = _actions(incident, hits)
    verification = _verification(hits, actions)
    trace = [
        "MonitorAgent reviewed incident severity, status, and category.",
        f"RetrieverAgent returned {len(hits)} PeacePulse runbook matches.",
        f"DiagnosisAgent produced {len(hypotheses)} conservative hypotheses.",
        f"PlannerAgent produced {len(actions)} recommended actions.",
        "VerifierAgent checked privacy boundaries and citation coverage.",
    ]
    return {
        "incident_id": incident.id,
        "summary": f"{incident.category.replace('_', ' ').title()} concern at site {incident.site_id} with severity {incident.severity}.",
        "hypotheses": hypotheses,
        "recommended_actions": actions,
        "verification": verification,
        "citations": [_citation(hit) for hit in hits],
        "agent_trace": trace,
    }


def create_session(db: Session, org_id: str, site_ids: set[str], payload: Any) -> dict[str, Any]:
    incident = None
    if payload.incident_id:
        incident = db.get(models.Incident, payload.incident_id)
        if not incident or incident.organization_id != org_id or (site_ids and incident.site_id not in site_ids):
            raise ValueError("Incident not found.")
    session = models.CopilotSession(
        id=new_id("cps"),
        organization_id=org_id,
        site_id=incident.site_id if incident else None,
        incident_id=incident.id if incident else None,
        title=payload.title.strip() or "PeacePulse copilot session",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session_out(db, session)


def list_sessions(db: Session, org_id: str, site_ids: set[str]) -> list[dict[str, Any]]:
    query = select(models.CopilotSession).where(models.CopilotSession.organization_id == org_id)
    if site_ids:
        query = query.where((models.CopilotSession.site_id.is_(None)) | (models.CopilotSession.site_id.in_(site_ids)))
    rows = db.scalars(query.order_by(models.CopilotSession.updated_at.desc()).limit(30))
    return [session_out(db, row, include_messages=False) for row in rows]


def session_out(db: Session, session: models.CopilotSession, include_messages: bool = True) -> dict[str, Any]:
    messages = []
    if include_messages:
        rows = db.scalars(select(models.CopilotMessage).where(models.CopilotMessage.session_id == session.id).order_by(models.CopilotMessage.created_at))
        messages = [_message_out(row) for row in rows]
    return {
        "id": session.id,
        "incident_id": session.incident_id,
        "title": session.title,
        "status": session.status,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "messages": messages,
    }


def add_message(db: Session, session: models.CopilotSession, content: str) -> dict[str, Any]:
    ensure_seed_runbooks(db)
    user_message = models.CopilotMessage(
        id=new_id("cpm"),
        session_id=session.id,
        organization_id=session.organization_id,
        role="user",
        content=redact(content),
    )
    db.add(user_message)
    assistant = _assistant_turn(db, session, content)
    db.add(assistant)
    session.updated_at = now_utc()
    db.commit()
    db.refresh(session)
    return session_out(db, session)


def retrieve_context(db: Session, org_id: str, query: str, limit: int = 5) -> list[RetrievalHit]:
    ensure_seed_runbooks(db)
    runbooks = list(
        db.scalars(
            select(models.CopilotRunbook).where(
                (models.CopilotRunbook.organization_id.is_(None)) | (models.CopilotRunbook.organization_id == org_id)
            )
        )
    )
    query_vector = _vectorize(query)
    hits: list[RetrievalHit] = []
    for runbook in runbooks:
        vector = _stored_vector(runbook)
        score = _cosine(query_vector, vector)
        if score <= 0:
            continue
        hits.append(RetrievalHit(runbook.id, runbook.title, runbook.category, round(score, 4), runbook.content[:260]))
    return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]


def _assistant_turn(db: Session, session: models.CopilotSession, content: str) -> models.CopilotMessage:
    incident = db.get(models.Incident, session.incident_id) if session.incident_id else None
    query = content if not incident else f"{content} {_incident_query(incident)}"
    hits = retrieve_context(db, session.organization_id, query, limit=4)
    citations = [_citation(hit) for hit in hits]
    if incident:
        investigation = investigate_incident(db, incident)
        answer = (
            f"I reviewed incident {incident.id}. {investigation['summary']} "
            f"Top recommendation: {investigation['recommended_actions'][0]}"
        )
        payload = investigation
    else:
        topics = ", ".join(hit.title for hit in hits[:3]) or "no matching runbooks"
        answer = f"I searched the PeacePulse runbooks for that question. Matching context: {topics}."
        payload = {"query": redact(content), "matched_runbooks": [hit.document_id for hit in hits]}
    return models.CopilotMessage(
        id=new_id("cpm"),
        session_id=session.id,
        organization_id=session.organization_id,
        role="assistant",
        content=answer,
        citations_json=json.dumps(citations),
        action_payload_json=json.dumps(payload, default=str),
    )


def _runbook_out(runbook: models.CopilotRunbook) -> dict[str, Any]:
    return {
        "id": runbook.id,
        "title": runbook.title,
        "category": runbook.category,
        "source": runbook.source,
        "tags": json.loads(runbook.tags_json),
        "excerpt": runbook.content[:220],
        "content": runbook.content,
        "retrieval_method": "local_tfidf_cosine",
        "created_at": runbook.created_at,
    }


def _message_out(message: models.CopilotMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": json.loads(message.citations_json),
        "action_payload": json.loads(message.action_payload_json),
        "created_at": message.created_at,
    }


def _incident_query(incident: models.Incident) -> str:
    return f"{incident.category} {incident.severity} {incident.redacted_text} {incident.cluster_key} {' '.join(json.loads(incident.keywords_json))}"


def _hypotheses(incident: models.Incident, hits: list[RetrievalHit]) -> list[str]:
    base = [f"{incident.category.replace('_', ' ')} pattern requires human review"]
    if incident.severity >= 4:
        base.append("high-severity signal may need immediate steward escalation")
    if "resource" in incident.category:
        base.append("service-point pressure or perceived unfairness may be driving conflict")
    if "rumor" in incident.category:
        base.append("unverified claims may amplify safety risk if not answered calmly")
    base.extend(f"retrieved context: {hit.title}" for hit in hits[:2])
    return list(dict.fromkeys(base))[:5]


def _actions(incident: models.Incident, hits: list[RetrievalHit]) -> list[str]:
    actions = ["Review the redacted report and assign a steward-owned next step."]
    if hits:
        actions.append(f"Follow {hits[0].document_id}: {hits[0].excerpt.split('.')[0].strip()}.")
    if incident.severity >= 4:
        actions.append("Prioritize safety check-in before public messaging.")
    actions.append("Keep public updates coarse and non-identifying.")
    return actions


def _verification(hits: list[RetrievalHit], actions: list[str]) -> dict[str, Any]:
    return {
        "passed": bool(actions),
        "checks": [
            "recommendations are limited to human-review support",
            "citations are from local PeacePulse runbooks" if hits else "no runbook match found; fallback guidance used",
            "no raw report text or identity details included",
        ],
        "warnings": [] if hits else ["Add more local runbooks for stronger grounding."],
    }


def _citation(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "document_id": hit.document_id,
        "title": hit.title,
        "category": hit.category,
        "score": hit.score,
        "excerpt": hit.excerpt,
        "retrieval_method": hit.retrieval_method,
    }


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[a-zA-Z0-9_]+", text)]


def _runbook_text(title: str, category: str, tags: list[str], content: str) -> str:
    return f"{title} {category} {' '.join(tags)} {content}"


def _vectorize(text: str) -> dict[str, float]:
    counts = Counter(token for token in _tokens(text) if len(token) > 2)
    total = sum(counts.values()) or 1
    return {token: round(count / total, 6) for token, count in counts.items()}


def _stored_vector(runbook: models.CopilotRunbook) -> dict[str, float]:
    try:
        vector = json.loads(runbook.embedding_json or "{}")
    except json.JSONDecodeError:
        vector = {}
    if vector:
        return {str(key): float(value) for key, value in vector.items()}
    tags = json.loads(runbook.tags_json)
    return _vectorize(_runbook_text(runbook.title, runbook.category, tags, runbook.content))


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    numerator = sum(left.get(token, 0.0) * right.get(token, 0.0) for token in left)
    if numerator <= 0:
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return numerator / (left_norm * right_norm)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
