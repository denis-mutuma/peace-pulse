from __future__ import annotations

import re

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


def detect_anomaly(queue_length: int, flow_rate: float, uptime: int) -> str:
    flags = []
    if uptime == 0:
        flags.append("pump offline")
    if queue_length >= 40:
        flags.append("queue pressure")
    if flow_rate < 1.0:
        flags.append("low flow")
    return ", ".join(flags) if flags else "normal"
