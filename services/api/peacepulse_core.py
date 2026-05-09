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
DB_PATH = Path(os.environ.get("PEACEPULSE_DB_PATH", DATA_DIR / "peacepulse.db"))
EVIDENCE_DIR = DATA_DIR / "storage" / "evidence"
ALLOWED_LANGUAGES = {"en", "sw", "fr", "ar"}
MAX_REPORT_TEXT_LENGTH = 2000
MAX_EVIDENCE_BYTES = 2_000_000
ALLOWED_EVIDENCE_MIME_PREFIXES = ("image/", "audio/", "text/", "application/pdf")

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
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[redacted-email]"),
    (re.compile(r"\b(?:\+?\d[\d\-\s()]{7,}\d)\b"), "[redacted-phone]"),
    (re.compile(r"\b[A-Z]{2,}\d{5,}\b"), "[redacted-id]"),
    (re.compile(r"\b(?:block|unit|tent|shelter|house)\s+[A-Z0-9-]{1,12}\b", re.IGNORECASE), "[redacted-location]"),
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


def configure_from_env() -> None:
    global DATA_DIR, DB_PATH, EVIDENCE_DIR
    if db_path := os.environ.get("PEACEPULSE_DB_PATH"):
        DB_PATH = Path(db_path)
        DATA_DIR = DB_PATH.parent
        EVIDENCE_DIR = DATA_DIR / "storage" / "evidence"


def init_db(seed_demo_data: bool = False) -> None:
    configure_from_env()
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
                redacted_text TEXT NOT NULL DEFAULT '',
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

            CREATE TABLE IF NOT EXISTS status_events (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                previous_status TEXT NOT NULL,
                new_status TEXT NOT NULL,
                actor_label TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS incident_notes (
                id TEXT PRIMARY KEY,
                incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                actor_label TEXT NOT NULL,
                note TEXT NOT NULL
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
                actor_label TEXT NOT NULL,
                action TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS resource_events (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                queue_length INTEGER NOT NULL,
                flow_rate REAL NOT NULL,
                uptime INTEGER NOT NULL,
                maintenance_note TEXT NOT NULL DEFAULT '',
                anomaly TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS rumors (
                id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                language TEXT NOT NULL,
                rough_location TEXT NOT NULL,
                text TEXT NOT NULL,
                redacted_text TEXT NOT NULL,
                severity INTEGER NOT NULL,
                cluster_key TEXT NOT NULL,
                response_notes TEXT NOT NULL DEFAULT ''
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
        columns = {row["name"] for row in con.execute("PRAGMA table_info(reports)").fetchall()}
        if "redacted_text" not in columns:
            con.execute("ALTER TABLE reports ADD COLUMN redacted_text TEXT NOT NULL DEFAULT ''")
        if seed_demo_data and con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0:
            seed_demo(con)


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(row) for row in con.execute(sql, params).fetchall()]


def health_status() -> dict[str, Any]:
    with connect() as con:
        con.execute("SELECT 1").fetchone()
    return {"ok": True, "service": "peacepulse-edge", "database": "ok", "sync": sync_status()}


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
    create_resource_event(
        {"resource_id": "water-point-north", "queue_length": 55, "flow_rate": 0.4, "uptime": 0},
        con=con,
    )
    create_rumor(
        {
            "language": "en",
            "rough_location": "North water point",
            "text": "People say aid is being diverted before it reaches the water point.",
        },
        con=con,
    )


def public_report(report: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in report.items() if key != "text"}


def create_report(data: dict[str, Any], con: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = con is None
    con = con or connect()
    language = str(data.get("language") or "en")[:12]
    category_hint = str(data.get("category_hint") or "")[:40]
    text = str(data.get("text") or "").strip()
    if language not in ALLOWED_LANGUAGES:
        raise ValueError("Invalid language.")
    if category_hint and category_hint not in REPORT_CATEGORIES:
        raise ValueError("Invalid concern type.")
    if len(text) < 8:
        raise ValueError("Report text must be at least 8 characters.")
    if len(text) > MAX_REPORT_TEXT_LENGTH:
        raise ValueError("Report text must be 2,000 characters or fewer.")
    report = {
        "id": new_id("rep"),
        "created_at": now(),
        "language": language,
        "rough_location": str(data.get("rough_location") or "unspecified")[:80],
        "category_hint": category_hint,
        "text": text,
        "redacted_text": redact(text),
    }
    con.execute(
        """
        INSERT INTO reports (id, created_at, language, rough_location, category_hint, text, redacted_text)
        VALUES (:id, :created_at, :language, :rough_location, :category_hint, :text, :redacted_text)
        """,
        report,
    )
    if owns:
        con.commit()
        con.close()
    return report


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
    enqueue_sync(con, "incident_summary", incident["id"], incident)
    if owns:
        con.commit()
        con.close()
    return incident


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


def list_status_history(incident_id: str) -> list[dict[str, Any]]:
    return rows(
        """
        SELECT *
        FROM status_events
        WHERE incident_id = ?
        ORDER BY created_at, id
        """,
        (incident_id,),
    )


def update_incident_status(incident_id: str, status: str, actor_label: str = "responder") -> dict[str, Any]:
    allowed = {"new", "assigned", "in_progress", "resolved"}
    if status not in allowed:
        raise ValueError("Invalid status.")
    with connect() as con:
        row = con.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if not row:
            raise ValueError("Incident not found.")
        previous_status = row["status"]
        if previous_status != status:
            con.execute("UPDATE incidents SET status = ? WHERE id = ?", (status, incident_id))
            con.execute(
                """
                INSERT INTO status_events (id, incident_id, created_at, previous_status, new_status, actor_label)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_id("ste"), incident_id, now(), previous_status, status, actor_label[:80] or "responder"),
            )
        updated = con.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        return dict(updated)


def create_incident_note(incident_id: str, data: dict[str, Any]) -> dict[str, Any]:
    note = str(data.get("note") or "").strip()
    actor_label = str(data.get("actor_label") or "responder").strip()[:80] or "responder"
    if len(note) < 4:
        raise ValueError("Note must be at least 4 characters.")
    if len(note) > 500:
        raise ValueError("Note must be 500 characters or fewer.")
    with connect() as con:
        if not con.execute("SELECT 1 FROM incidents WHERE id = ?", (incident_id,)).fetchone():
            raise ValueError("Incident not found.")
        record = {
            "id": new_id("nte"),
            "incident_id": incident_id,
            "created_at": now(),
            "actor_label": actor_label,
            "note": redact(note),
        }
        con.execute(
            """
            INSERT INTO incident_notes (id, incident_id, created_at, actor_label, note)
            VALUES (:id, :incident_id, :created_at, :actor_label, :note)
            """,
            record,
        )
        enqueue_sync(con, "incident_note", record["id"], record)
        return record


def list_incident_notes(incident_id: str) -> list[dict[str, Any]]:
    return rows(
        """
        SELECT *
        FROM incident_notes
        WHERE incident_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (incident_id,),
    )


def purge_triaged_report_text() -> int:
    with connect() as con:
        result = con.execute(
            """
            UPDATE reports
            SET text = ''
            WHERE status = 'triaged' AND text != ''
            """
        )
        return result.rowcount


def xor_bytes(data: bytes) -> bytes:
    key = hashlib.sha256(b"peacepulse-demo-local-key").digest()
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def decode_evidence_content(content_base64: str) -> bytes:
    raw_b64 = str(content_base64 or "").strip()
    if "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(raw_b64, validate=True)
    except ValueError as exc:
        raise ValueError("Evidence content must be valid base64.") from exc
    if not raw:
        raise ValueError("Evidence content is required.")
    if len(raw) > MAX_EVIDENCE_BYTES:
        raise ValueError("Evidence file must be 2 MB or smaller.")
    return raw


def validate_evidence_metadata(data: dict[str, Any]) -> tuple[str, str]:
    filename = os.path.basename(str(data.get("filename") or "evidence.bin")).strip()[:120]
    mime_type = str(data.get("mime_type") or "application/octet-stream").strip().lower()[:120]
    if not filename or filename in {".", ".."}:
        raise ValueError("Evidence filename is required.")
    if not re.fullmatch(r"[\w .()+,-]{1,120}", filename):
        raise ValueError("Evidence filename contains unsupported characters.")
    if not any(mime_type.startswith(prefix) for prefix in ALLOWED_EVIDENCE_MIME_PREFIXES):
        raise ValueError("Evidence file type is not supported.")
    return filename, mime_type


def create_evidence(data: dict[str, Any]) -> dict[str, Any]:
    raw = decode_evidence_content(data.get("content_base64") or "")
    filename, mime_type = validate_evidence_metadata(data)
    evidence_id = new_id("evd")
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
        "mime_type": mime_type,
        "sha256": hashlib.sha256(raw).hexdigest(),
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
        add_custody_event(con, evidence_id, "community_submitter", "uploaded, hashed, and locally encrypted")
        if record["sync_allowed"]:
            enqueue_sync(con, "evidence_record", evidence_id, {k: v for k, v in record.items() if k != "encrypted_path"})
    return record


def add_custody_event(con: sqlite3.Connection, evidence_id: str, actor_label: str, action: str) -> None:
    con.execute(
        "INSERT INTO custody_events (id, evidence_id, created_at, actor_label, action) VALUES (?, ?, ?, ?, ?)",
        (new_id("coe"), evidence_id, now(), actor_label[:80] or "steward", action[:160]),
    )


def list_evidence() -> list[dict[str, Any]]:
    items = rows("SELECT * FROM evidence ORDER BY created_at DESC")
    for item in items:
        item["custody"] = rows("SELECT * FROM custody_events WHERE evidence_id = ? ORDER BY created_at", (item["id"],))
    return items


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
    event = {
        "id": new_id("res"),
        "created_at": now(),
        "resource_id": str(data.get("resource_id") or "water-point-north")[:80],
        "queue_length": int(data.get("queue_length") or 0),
        "flow_rate": float(data.get("flow_rate") or 0),
        "uptime": 1 if int(data.get("uptime", 1)) else 0,
        "maintenance_note": str(data.get("maintenance_note") or "")[:160],
    }
    event["anomaly"] = detect_anomaly(event["queue_length"], event["flow_rate"], event["uptime"])
    con.execute(
        """
        INSERT INTO resource_events
        (id, created_at, resource_id, queue_length, flow_rate, uptime, maintenance_note, anomaly)
        VALUES (:id, :created_at, :resource_id, :queue_length, :flow_rate, :uptime, :maintenance_note, :anomaly)
        """,
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


def create_rumor(data: dict[str, Any], con: sqlite3.Connection | None = None) -> dict[str, Any]:
    owns = con is None
    con = con or connect()
    text = str(data.get("text") or "").strip()
    if len(text) < 8:
        raise ValueError("Rumor text must be at least 8 characters.")
    language = str(data.get("language") or "en")[:12]
    if language not in ALLOWED_LANGUAGES:
        raise ValueError("Invalid language.")
    key_terms = keywords(text)
    rumor = {
        "id": new_id("rum"),
        "created_at": now(),
        "language": language,
        "rough_location": str(data.get("rough_location") or "unspecified")[:80],
        "text": text,
        "redacted_text": redact(text),
        "severity": severity_score(text, "rumor"),
        "cluster_key": cluster_key("rumor", str(data.get("rough_location") or "unspecified"), key_terms),
        "response_notes": str(data.get("response_notes") or "")[:240],
    }
    con.execute(
        """
        INSERT INTO rumors
        (id, created_at, language, rough_location, text, redacted_text, severity, cluster_key, response_notes)
        VALUES (:id, :created_at, :language, :rough_location, :text, :redacted_text, :severity, :cluster_key, :response_notes)
        """,
        rumor,
    )
    enqueue_sync(con, "rumor_summary", rumor["id"], {k: v for k, v in rumor.items() if k != "text"})
    if owns:
        con.commit()
        con.close()
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
        cluster["items"] = rows(
            """
            SELECT id, created_at, language, rough_location, redacted_text, severity, response_notes
            FROM rumors
            WHERE cluster_key = ?
            ORDER BY created_at DESC
            """,
            (cluster["cluster_key"],),
        )
    return clusters


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


def sync_preview(limit: int = 20) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 20), 100))
    items = rows(
        """
        SELECT id, created_at, item_type, item_id, payload, status, synced_at
        FROM sync_queue
        ORDER BY created_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [public_sync_item(item) for item in items]


def public_sync_item(item: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(item["payload"])
    return {
        "id": item["id"],
        "created_at": item["created_at"],
        "item_type": item["item_type"],
        "item_id": item["item_id"],
        "status": item["status"],
        "synced_at": item["synced_at"],
        "payload_keys": sorted(payload.keys()),
        "summary": sync_payload_summary(item["item_type"], payload),
    }


def sync_payload_summary(item_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if item_type == "incident_summary":
        return {
            "category": payload.get("category"),
            "severity": payload.get("severity"),
            "cluster_key": payload.get("cluster_key"),
            "redacted_text": payload.get("redacted_text"),
        }
    if item_type == "evidence_record":
        return {
            "filename": payload.get("filename"),
            "mime_type": payload.get("mime_type"),
            "sha256": payload.get("sha256"),
            "size_bytes": payload.get("size_bytes"),
        }
    if item_type == "resource_anomaly":
        return {
            "resource_id": payload.get("resource_id"),
            "anomaly": payload.get("anomaly"),
            "queue_length": payload.get("queue_length"),
            "uptime": payload.get("uptime"),
        }
    if item_type == "rumor_summary":
        return {
            "rough_location": payload.get("rough_location"),
            "severity": payload.get("severity"),
            "cluster_key": payload.get("cluster_key"),
            "redacted_text": payload.get("redacted_text"),
        }
    if item_type == "incident_note":
        return {
            "incident_id": payload.get("incident_id"),
            "actor_label": payload.get("actor_label"),
            "note": payload.get("note"),
        }
    return {"item_type": item_type}


def run_sync() -> dict[str, Any]:
    synced = 0
    with connect() as con:
        pending = con.execute("SELECT id FROM sync_queue WHERE status = 'pending' ORDER BY created_at").fetchall()
        for item in pending:
            con.execute("UPDATE sync_queue SET status = 'synced', synced_at = ? WHERE id = ?", (now(), item["id"]))
            synced += 1
    return {"synced": synced, **sync_status()}
