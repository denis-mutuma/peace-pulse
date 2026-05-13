from __future__ import annotations

import re


SENSITIVE_PATTERNS = [
    (re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE), "[redacted-email]"),
    (re.compile(r"\b(?:\+?\d[\d\-\s()]{7,}\d)\b"), "[redacted-phone]"),
    (re.compile(r"\b[A-Z]{2,}\d{5,}\b"), "[redacted-id]"),
    (re.compile(r"\b(?:block|unit|tent|shelter|house|room)\s+[A-Z0-9-]{1,12}\b", re.IGNORECASE), "[redacted-location]"),
    (re.compile(r"\b(?:near|at|behind|opposite)\s+[A-Z][A-Za-z0-9\s]{3,40}"), "[redacted-location]"),
    (re.compile(r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+\b"), "[redacted-name]"),
]


def redact(text: str) -> str:
    redacted = text
    for pattern, replacement in SENSITIVE_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z]{4,}", text.lower())
    stop = {"there", "because", "being", "with", "from", "after", "some", "people", "report", "families"}
    ranked: list[str] = []
    for word in words:
        if word not in stop and word not in ranked:
            ranked.append(word)
    return ranked[:8]
