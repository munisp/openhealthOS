"""ngpatterns.py — Nigerian PII pattern pack, OpenMed PIIPattern-compatible.

OpenMed ships 35+ language PII packs but none for Nigeria yet (ADR-0065).
This pack is written to OpenMed's PIIPattern field spec so it can be
registered upstream unchanged (it is also our candidate upstream
contribution). Detection here is deterministic regex — no model download,
no network, edge-honest. When the OpenMed model path is live, these
patterns remain as the high-precision deterministic layer the models
fall back on for Nigerian identifiers.

Fields mirror openmed.PIIPattern: pattern, entity_type, priority,
base_score, context_boost, context_words.
"""
import re

NG_PATTERNS = [
    {
        "pattern": r"\b\d{11}\b",
        "entity_type": "nin",
        "priority": 10,
        "base_score": 0.4,
        "context_boost": 0.5,
        "context_words": ["nin", "national identity", "national id", "nimc", "identity number"],
        # 11 bare digits only score high with context — honest precision:
        # a bare 11-digit string might be a phone fragment or a lab number.
    },
    {
        "pattern": r"(?<!\d)(?:\+234[\s-]?|0)(?:7\d|8\d|9\d)[\s-]?\d{4}[\s-]?\d{4}(?!\d)",
        "entity_type": "phone_ng",
        "priority": 9,
        "base_score": 0.85,
        "context_boost": 0.15,
        "context_words": ["phone", "call", "mobile", "gsm", "whatsapp", "contact"],
    },
    {
        "pattern": r"\bOHF-[A-Z]{2}-[A-Z]{2,5}-\d{4}\b",
        "entity_type": "facility_id",
        "priority": 8,
        "base_score": 0.9,
        "context_boost": 0.1,
        "context_words": ["facility", "clinic", "phc", "hospital", "attended"],
        # facility ids are pseudonymous, not patient PII — but in small
        # cohorts "the only patient at OHF-XX-..." re-identifies, so the
        # gold layer treats them as quasi-identifiers. Honest inclusion.
    },
    {
        "pattern": r"\bNHIA-\d{6,10}\b",
        "entity_type": "nhia_enrollee_id",
        "priority": 10,
        "base_score": 0.9,
        "context_boost": 0.1,
        "context_words": ["nhia", "enrollee", "insurance", "hmo", "member"],
    },
    {
        "pattern": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "entity_type": "email",
        "priority": 10,
        "base_score": 0.95,
        "context_boost": 0.05,
        "context_words": ["email", "mail", "contact"],
    },
]

CONTEXT_WINDOW = 40  # chars around the match scanned for context words


def detect(text):
    """Deterministic Nigerian PII detection.

    Returns spans shaped like NER output: {start, end, text, label, score}
    — the same shape OpenMed model detections arrive in, so the rest of
    the pipeline never knows which detector produced them.
    """
    spans = []
    for p in NG_PATTERNS:
        for m in re.finditer(p["pattern"], text, re.I):
            score = p["base_score"]
            lo = max(0, m.start() - CONTEXT_WINDOW)
            ctx = text[lo:m.end() + CONTEXT_WINDOW].lower()
            if any(w in ctx for w in p["context_words"]):
                score = min(1.0, score + p["context_boost"])
            spans.append({"start": m.start(), "end": m.end(),
                          "text": m.group(0), "label": p["entity_type"],
                          "score": round(score, 3)})
    # Resolve overlaps: an 11-digit phone number also matches the bare
    # ``\d{11}`` NIN pattern. Overlapping spans must never both reach the
    # redactor — double replacement at the same coordinates eats clinical
    # text. Keep the higher-scoring span (phone_ng out-scores a contextless
    # NIN), drop the loser. Ties keep the earlier span.
    spans.sort(key=lambda s: s["start"])
    kept = []
    for s in sorted(spans, key=lambda s: -s["score"]):
        if any(s["start"] < k["end"] and k["start"] < s["end"] for k in kept):
            continue
        kept.append(s)
    kept.sort(key=lambda s: s["start"])
    return kept
