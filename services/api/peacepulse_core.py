from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "peacepulse.db"
EVIDENCE_DIR = DATA_DIR / "storage" / "evidence"

REPORT_CATEGORIES = {
    "resource": ["water", "queue", "pump", "food", "stock", "clinic", "distribution", "solar"],
    "threat": ["threat", "attack", "intimidat", "violence", "weapon", "unsafe"],
    "corruption": ["bribe", "favor", "divert", "stolen", "corrupt", "abuse"],
    "service_denial": ["denied", "turned away", "refused", "blocked", "excluded"],
    "rumor": ["rumor", "heard", "people say", "claim", "spreading"],
    "unsafe_route": ["road", "route", "checkpoint", "blocked", "bridge"],
    "work_exploitation": ["work", "wage", "pay", "exploitation", "job"],
}

SENSITIVE_PATTERNS = [
    (re.compile(r"\b(?:\+?\d[\d\-\s()]{7,}\d)\b"), "[redacted-phone]"),
    (re.compile(r"\b[A-Z]{2,}\d{5,}\b"), "[redacted-id]"),
    (re.compile(r"\b(?:near|at|behind|opposite)\s+[A-Z][A-Za-z0-9\s]{3,40}"), "[redacted-location]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Dr)\.?\s+[A-Z][a-z]+\b"), "[redacted-name]"),
]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db() -> None:
    with connect() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                language TEXT NOT NULL,
                rough_location TEXT NOT NULL,
                category_hint TEXT,
                text TEXT NOT NULL,
                consent_to_sync INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'queued'
            );

            CREATE TABLE IF NOT EXISTS incidents (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                category TEXT NOT NULL,
                severity INTEGER NOT NULL,
                confidence REAL NOT NULL,
                redacted_text TEXT NOT NULL,
                keywords TEXT NOT NULL,
                cluster_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                public_update TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notes (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                author_role TEXT NOT NULL,
                text TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                encrypted_path TEXT NOT NULL,
                linked_report_id TEXT,
                sync_allowed INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS custody_events (
                id TEXT PRIMARY KEY,
                evidence_id TEXT NOT NULL REFERENCES evidence(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rumors (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                language TEXT NOT NULL,
                rough_location TEXT NOT NULL,
                text TEXT NOT NULL,
                severity INTEGER NOT NULL,
                cluster_key TEXT NOT NULL,
                response_notes TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS resource_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                queue_length INTEGER NOT NULL,
                flow_rate REAL NOT NULL,
                uptime INTEGER NOT NULL,
                anomaly TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sync_queue (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                synced_at TEXT
            );
            """
        )
        if con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0:
            seed_demo(con)


def seed_demo(con: sqlite3.Connection) -> None:
    samples = [
        {
            "language": "en",
            "rough_location": "North water point",
            "category_hint": "resource",
            "text": "There is tension at the main water point because some families are being turned away after long queues.",
        },
        {
            "language": "sw",
            "rough_location": "North water point",
            "category_hint": "rumor",
            "text": "People say aid is being diverted and one group gets water first.",
        },
    ]
    for sample in samples:
        report = create_report(sample, con=con)
        triage_report(report["id"], con=con)
    for queue_length, flow_rate, uptime in [(12, 8.5, 1), (44, 2.1, 1), (59, 0.2, 0)]:
        create_resource_event(
            {
                "resource_id": "water-point-north",
                "queue_length": queue_length,
                "flow_rate": flow_rate,
                "uptime": uptime,
            },
            con=con,
        )


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(row) for row in con.execute(sql, params).fetchall()]


def create_report(data: dict[str, Any], con: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = con is None
    con = con or connect()
    report_id = new_id("rep")
    payload = {
        "id": report_id,
        "created_at": now(),
        "language": str(data.get("language") or "en")[:12],
        "rough_location": str(data.get("rough_location") or "unspecified")[:80],
        "category_hint": str(data.get("category_hint") or "")[:40],
        "text": str(data.get("text") or "").strip(),
        "consent_to_sync": 1 if data.get("consent_to_sync") else 0,
    }
    if len(payload["text"]) < 8:
        raise ValueError("Report text must be at least 8 characters.")
    con.execute(
        """
        INSERT INTO reports (id, created_at, language, rough_location, category_hint, text, consent_to_sync)
        VALUES (:id, :created_at, :language, :rough_location, :category_hint, :text, :consent_to_sync)
        """,
        payload,
    )
    if owns:
        con.commit()
        con.close()
    return payload


def classify(text: str, hint: str = "") -> tuple[str, float]:
    lower = f"{hint} {text}".lower()
    scores = {
        category: sum(1 for keyword in keywords if keyword in lower)
        for category, keywords in REPORT_CATEGORIES.items()
    }
    if hint in REPORT_CATEGORIES:
        scores[hint] = scores.get(hint, 0) + 3
    category, score = max(scores.items(), key=lambda item: item[1])
    if score == 0:
        return "other", 0.35
    return category, min(0.95, 0.45 + score * 0.18)


def redact(text: str) -> str:
    redacted = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    stop = {"there", "because", "being", "with", "from", "after", "some", "people", "report", "families"}
    ranked = []
    for word in words:
        if word not in stop and word not in ranked:
            ranked.append(word)
    return ranked[:8]


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


def make_public_update(category: str, location: str) -> str:
    label = category.replace("_", " ")
    return (
        f"Community stewards are reviewing a {label} concern near {location}. "
        "Please use verified service points and avoid sharing identifying details."
    )


def triage_report(report_id: str, con: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = con is None
    con = con or connect()
    report = con.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not report:
        raise ValueError("Report not found.")
    existing = con.execute("SELECT * FROM incidents WHERE report_id = ?", (report_id,)).fetchone()
    if existing:
        result = dict(existing)
        if owns:
            con.close()
        return result

    category, confidence = classify(report["text"], report["category_hint"])
    redacted_text = redact(report["text"])
    key_terms = keywords(redacted_text)
    incident = {
        "id": new_id("inc"),
        "report_id": report_id,
        "created_at": now(),
        "category": category,
        "severity": severity_score(report["text"], category),
        "confidence": confidence,
        "redacted_text": redacted_text,
        "keywords": json.dumps(key_terms),
        "cluster_key": cluster_key(category, report["rough_location"], key_terms),
        "public_update": make_public_update(category, report["rough_location"]),
    }
    con.execute(
        """
        INSERT INTO incidents
        (id, report_id, created_at, category, severity, confidence, redacted_text, keywords, cluster_key, public_update)
        VALUES (:id, :report_id, :created_at, :category, :severity, :confidence, :redacted_text, :keywords, :cluster_key, :public_update)
        """,
        incident,
    )
    con.execute("UPDATE reports SET status = 'triaged' WHERE id = ?", (report_id,))
    if report["consent_to_sync"]:
        enqueue_sync(con, "incident_summary", incident["id"], incident)
    if owns:
        con.commit()
        con.close()
    return incident


def triage_pending(limit: int = 20) -> int:
    count = 0
    with connect() as con:
        for row in con.execute("SELECT id FROM reports WHERE status = 'queued' ORDER BY created_at LIMIT ?", (limit,)):
            triage_report(row["id"], con=con)
            count += 1
    return count


def list_reports() -> list[dict[str, Any]]:
    items = rows(
        """
        SELECT id, created_at, category_hint, status, text
        FROM reports
        ORDER BY created_at DESC
        """
    )
    for item in items:
        item["redacted_text"] = redact(item.pop("text"))
    return items


def list_incidents() -> list[dict[str, Any]]:
    items = rows(
        """
        SELECT i.*, r.language, r.rough_location
        FROM incidents i JOIN reports r ON r.id = i.report_id
        ORDER BY i.created_at DESC
        """
    )
    for item in items:
        item["keywords"] = json.loads(item["keywords"])
    return items


def update_incident_status(incident_id: str, status: str) -> dict[str, Any]:
    allowed = {"new", "assigned", "in_progress", "resolved"}
    if status not in allowed:
        raise ValueError("Invalid status.")
    with connect() as con:
        con.execute("UPDATE incidents SET status = ? WHERE id = ?", (status, incident_id))
        row = con.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if not row:
            raise ValueError("Incident not found.")
        return dict(row)


def add_note(incident_id: str, text: str, author_role: str = "steward") -> dict[str, Any]:
    note = {
        "id": new_id("note"),
        "incident_id": incident_id,
        "created_at": now(),
        "author_role": author_role,
        "text": text.strip(),
    }
    if len(note["text"]) < 2:
        raise ValueError("Note text is required.")
    with connect() as con:
        con.execute(
            "INSERT INTO notes (id, incident_id, created_at, author_role, text) VALUES (:id, :incident_id, :created_at, :author_role, :text)",
            note,
        )
    return note


def xor_bytes(data: bytes) -> bytes:
    key = hashlib.sha256(b"peacepulse-demo-local-key").digest()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def create_evidence(data: dict[str, Any]) -> dict[str, Any]:
    raw_b64 = data.get("content_base64") or ""
    if "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    raw = base64.b64decode(raw_b64)
    if not raw:
        raise ValueError("Evidence content is required.")
    evidence_id = new_id("evd")
    filename = os.path.basename(str(data.get("filename") or "evidence.bin"))[:120]
    digest = hashlib.sha256(raw).hexdigest()
    encrypted_path = EVIDENCE_DIR / f"{evidence_id}.bin"
    encrypted_path.write_bytes(xor_bytes(raw))
    try:
        stored_path = str(encrypted_path.relative_to(ROOT))
    except ValueError:
        stored_path = str(encrypted_path)
    record = {
        "id": evidence_id,
        "created_at": now(),
        "filename": filename,
        "mime_type": str(data.get("mime_type") or "application/octet-stream")[:120],
        "sha256": digest,
        "size_bytes": len(raw),
        "encrypted_path": stored_path,
        "linked_report_id": data.get("linked_report_id") or "",
        "sync_allowed": 1 if data.get("sync_allowed") else 0,
    }
    with connect() as con:
        con.execute(
            """
            INSERT INTO evidence
            (id, created_at, filename, mime_type, sha256, size_bytes, encrypted_path, linked_report_id, sync_allowed)
            VALUES (:id, :created_at, :filename, :mime_type, :sha256, :size_bytes, :encrypted_path, :linked_report_id, :sync_allowed)
            """,
            record,
        )
        event = {
            "id": new_id("coe"),
            "evidence_id": evidence_id,
            "created_at": now(),
            "actor_role": "community_submitter",
            "action": "uploaded, hashed, and locally encrypted",
        }
        con.execute(
            "INSERT INTO custody_events (id, evidence_id, created_at, actor_role, action) VALUES (:id, :evidence_id, :created_at, :actor_role, :action)",
            event,
        )
        if record["sync_allowed"]:
            enqueue_sync(con, "evidence_record", evidence_id, {k: v for k, v in record.items() if k != "encrypted_path"})
    return record


def list_evidence() -> list[dict[str, Any]]:
    items = rows("SELECT * FROM evidence ORDER BY created_at DESC")
    for item in items:
        item["custody"] = rows("SELECT * FROM custody_events WHERE evidence_id = ? ORDER BY created_at", (item["id"],))
    return items


def create_rumor(data: dict[str, Any]) -> dict[str, Any]:
    text = str(data.get("text") or "").strip()
    if len(text) < 8:
        raise ValueError("Rumor text must be at least 8 characters.")
    key_terms = keywords(text)
    severity = severity_score(text, "rumor")
    rumor = {
        "id": new_id("rum"),
        "created_at": now(),
        "language": str(data.get("language") or "en")[:12],
        "rough_location": str(data.get("rough_location") or "unspecified")[:80],
        "text": text,
        "severity": severity,
        "cluster_key": cluster_key("rumor", str(data.get("rough_location") or "unspecified"), key_terms),
    }
    with connect() as con:
        con.execute(
            "INSERT INTO rumors (id, created_at, language, rough_location, text, severity, cluster_key) VALUES (:id, :created_at, :language, :rough_location, :text, :severity, :cluster_key)",
            rumor,
        )
        enqueue_sync(con, "rumor_summary", rumor["id"], {**rumor, "text": redact(rumor["text"])})
    return rumor


def list_rumor_clusters() -> list[dict[str, Any]]:
    clusters = rows(
        """
        SELECT cluster_key, COUNT(*) AS count, MAX(severity) AS max_severity, MAX(created_at) AS latest_at
        FROM rumors
        GROUP BY cluster_key
        ORDER BY max_severity DESC, count DESC
        """
    )
    for cluster in clusters:
        cluster["items"] = rows("SELECT * FROM rumors WHERE cluster_key = ? ORDER BY created_at DESC", (cluster["cluster_key"],))
    return clusters


def detect_anomaly(queue_length: int, flow_rate: float, uptime: int) -> str:
    flags = []
    if uptime == 0:
        flags.append("pump offline")
    if queue_length >= 40:
        flags.append("queue pressure")
    if flow_rate < 1.0:
        flags.append("low flow")
    return ", ".join(flags) if flags else "normal"


def create_resource_event(data: dict[str, Any], con: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = con is None
    con = con or connect()
    queue_length = int(data.get("queue_length") or 0)
    flow_rate = float(data.get("flow_rate") or 0)
    uptime = 1 if int(data.get("uptime", 1)) else 0
    event = {
        "id": new_id("res"),
        "created_at": now(),
        "resource_id": str(data.get("resource_id") or "water-point-north")[:80],
        "queue_length": queue_length,
        "flow_rate": flow_rate,
        "uptime": uptime,
        "anomaly": detect_anomaly(queue_length, flow_rate, uptime),
    }
    con.execute(
        "INSERT INTO resource_events (id, created_at, resource_id, queue_length, flow_rate, uptime, anomaly) VALUES (:id, :created_at, :resource_id, :queue_length, :flow_rate, :uptime, :anomaly)",
        event,
    )
    if event["anomaly"] != "normal":
        enqueue_sync(con, "resource_anomaly", event["id"], event)
    if owns:
        con.commit()
        con.close()
    return event


def resource_status() -> list[dict[str, Any]]:
    return rows(
        """
        SELECT r.*
        FROM resource_events r
        WHERE r.rowid = (
            SELECT latest.rowid
            FROM resource_events latest
            WHERE latest.resource_id = r.resource_id
            ORDER BY latest.created_at DESC, latest.rowid DESC
            LIMIT 1
        )
        ORDER BY r.resource_id
        """
    )


def enqueue_sync(con: sqlite3.Connection, item_type: str, item_id: str, payload: dict[str, Any]) -> None:
    con.execute(
        "INSERT INTO sync_queue (id, created_at, item_type, item_id, payload) VALUES (?, ?, ?, ?, ?)",
        (new_id("syn"), now(), item_type, item_id, json.dumps(payload)),
    )


def sync_status() -> dict[str, Any]:
    with connect() as con:
        rows_ = con.execute("SELECT status, COUNT(*) AS count FROM sync_queue GROUP BY status").fetchall()
    counts = {"pending": 0, "synced": 0}
    counts.update({row["status"]: row["count"] for row in rows_})
    return counts


def run_sync() -> dict[str, Any]:
    synced = 0
    with connect() as con:
        pending = con.execute("SELECT id FROM sync_queue WHERE status = 'pending' ORDER BY created_at").fetchall()
        for item in pending:
            con.execute("UPDATE sync_queue SET status = 'synced', synced_at = ? WHERE id = ?", (now(), item["id"]))
            synced += 1
    return {"synced": synced, **sync_status()}
