from __future__ import annotations

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
ALLOWED_LANGUAGES = {"en", "sw", "fr", "ar"}
MAX_REPORT_TEXT_LENGTH = 2000

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
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def configure_from_env() -> None:
    global DATA_DIR, DB_PATH
    if db_path := os.environ.get("PEACEPULSE_DB_PATH"):
        DB_PATH = Path(db_path)
        DATA_DIR = DB_PATH.parent


def init_db() -> None:
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
            """
        )
        columns = {row["name"] for row in con.execute("PRAGMA table_info(reports)").fetchall()}
        if "redacted_text" not in columns:
            con.execute("ALTER TABLE reports ADD COLUMN redacted_text TEXT NOT NULL DEFAULT ''")


def rows(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as con:
        return [dict(row) for row in con.execute(sql, params).fetchall()]


def health_status() -> dict[str, Any]:
    with connect() as con:
        con.execute("SELECT 1").fetchone()
    return {"ok": True, "service": "peacepulse-edge", "database": "ok"}


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
