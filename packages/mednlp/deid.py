"""deid.py — de-identification pipeline for the lakehouse (ADR-0065).

Two stages, one honest substitution point each:

1. DETECTION — today: deterministic Nigerian pattern pack (ngpatterns);
   when the OpenMed model path is live, openmed.extract_pii drops in at
   the marked seam and its spans merge with ours (same span shape).
2. REDACTION — mask/hash implemented to OpenMed's deidentify semantics
   (irreversible mask; HMAC-salted hash for linkage-preserving research
   extracts). The gold-layer gate uses REAL openmed.risk k-anonymity,
   executable offline today.

Gate doctrine: if openmed.risk is unavailable, the gate RAISES — a gold
release never ships because the privacy check was quietly skipped.
"""
import hashlib
import hmac as _hmac

from ngpatterns import detect


def redact(text, spans, method="mask", salt=b""):
    """Apply redaction right-to-left so offsets stay valid.
    method: "mask" -> [LABEL]; "hash" -> stable HMAC token (linkable
    within this salt, irreversible without it)."""
    out = text
    for s in sorted(spans, key=lambda x: x["start"], reverse=True):
        if method == "mask":
            repl = f"[{s['label'].upper()}]"
        elif method == "hash":
            tok = _hmac.new(salt, s["text"].encode(), hashlib.sha256).hexdigest()[:10]
            repl = f"<{s['label']}:{tok}>"
        else:
            raise ValueError(f"unknown method: {method}")
        out = out[:s["start"]] + repl + out[s["end"]:]
    return out


def deidentify_note(text, method="mask", salt=b"", min_score=0.5):
    """Full note-level de-id: detect -> threshold -> redact."""
    spans = [s for s in detect(text) if s["score"] >= min_score]
    return {"text": redact(text, spans, method, salt), "spans_redacted": len(spans)}


def gold_gate(rows, quasi_identifiers, k):
    """k-anonymity gate for gold aggregates via REAL openmed.risk.

    Returns {released, report, rows}. Violations trigger minimal
    suppression via openmed's proposal engine; rows below the floor are
    dropped, never generalized silently. openmed missing -> RuntimeError.
    """
    try:
        from openmed.risk import analyze_k_anonymity, propose_suppression, apply_suppression
    except ImportError as exc:  # fail-LOUD, never fail-open on privacy
        raise RuntimeError(
            "openmed.risk unavailable — gold privacy gate cannot run; "
            "refusing to release aggregates without it") from exc
    report = analyze_k_anonymity(rows, quasi_identifiers, target_k=k)
    if report.meets_target:
        return {"released": True, "report": report, "rows": rows,
                "suppressed": 0}
    proposal = propose_suppression(rows, quasi_identifiers, target_k=k)
    kept = apply_suppression(rows, proposal)
    return {"released": len(kept) > 0, "report": report, "rows": kept,
            "suppressed": report.record_count - len(kept)}
