"""Bridge (ADR-0014) — connect the champion models to the platform's
frauddetect package without frauddetect ever importing torch.

frauddetect speaks the provider-feed schema (amount_ngn, tariff_code,
provider_id, patient_id, ISO ts); the models speak the lakehouse schema
(amount_ngn, tariff_ngn, facility_id, member_id, code, qty, day, hour).
This module translates and exposes `scorer_for(registry)` — a callable
matching FraudDetector's ml_scorer seam.

Failure doctrine: any translation or inference error yields no ML advisory;
the detector then decides on rules alone.
"""
from __future__ import annotations

from datetime import datetime

from .infer import ChampionScorer

# Tariff lookup for the provider-feed schema, which carries a tariff CODE
# but not the NGN tariff amount the features need for amount_ratio.
from .data import TARIFF

_EPOCH = datetime(2026, 1, 1)


def to_ml_claim(claim: dict) -> dict:
    """Provider-feed claim → lakehouse-schema claim."""
    ts = claim.get("ts", "")
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
        day = max(0, (dt - _EPOCH).days)
        hour = dt.hour
    except Exception:
        day, hour = 0, 12
    code = claim.get("tariff_code", "")
    return {
        "claim_id": claim.get("claim_id", ""),
        "member_id": claim.get("patient_id", ""),
        "facility_id": claim.get("provider_id", ""),
        "scheme": claim.get("scheme", ""),
        "code": code,
        "qty": int(claim.get("qty", 1) or 1),
        "amount_ngn": float(claim.get("amount_ngn", 0) or 0),
        "tariff_ngn": float(TARIFF.get(code, 1500)),
        "day": day,
        "hour": hour,
        "fraud": False,
        "archetype": "",
    }


def scorer_for(registry: str | None = None):
    """Return a callable matching FraudDetector's ml_scorer seam:
    (claim, history) -> {"score", "model_version"} | None."""
    scorer = ChampionScorer(registry) if registry else ChampionScorer()

    def score_one(claim: dict, history: list[dict]) -> dict | None:
        try:
            window = [to_ml_claim(h) for h in (history or [])[-500:]]
            ml_claim = to_ml_claim(claim)
            # Features are window-relative (velocity, counts): score the
            # claim inside its history window and take the last row.
            out = scorer.score_claims(window + [ml_claim])
            return out[-1]
        except Exception:
            return None

    return score_one
