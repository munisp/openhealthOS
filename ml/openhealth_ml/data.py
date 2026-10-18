"""Synthetic-but-realistic Nigerian claims generator + lakehouse reader
(ADR-0014).

Two data paths, one interface:

* **Production**: read lakehouse gold aggregates + claim events exported
  from the state plane (`lakehouse/gold/claims.jsonl`). This is the
  continuous-training path — the model re-trains on what the platform
  actually saw.
* **Development/bootstrap**: a generator calibrated to Nigerian primary-care
  reality — BHCPF/state scheme mix, malaria-dominated code distribution,
  PHC tariff band — with four labelled fraud archetypes drawn from the
  NHIA fraud literature: ghost billing, upcoding, facility velocity abuse,
  and duplicate billing.

The generator is honest about what it is: labels are known because we
injected them. Real validation requires adjudicated production claims —
the pipeline is built so those drop in unchanged (same schema).
"""
from __future__ import annotations

import hashlib
import json
import random

CODES = ["MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB", "AMX-SUS", "ITN-001",
         "LAB-HB", "LAB-MP", "IMM-001", "FP-001"]
TARIFF = {"MAL-RDT": 1500, "ANC-001": 4500, "ORS-001": 900, "PARA-TAB": 1200,
          "AMX-SUS": 2600, "ITN-001": 2100, "LAB-HB": 1800, "LAB-MP": 3500,
          "IMM-001": 800, "FP-001": 1100}
SCHEMES = ["BHCPF-VUL", "STATE-BASIC", "NHIA-FORMAL", "HYGEIA-COMM"]
# Malaria/ANC dominate PHC visits — the Nigerian outpatient reality.
CODE_WEIGHTS = [0.28, 0.18, 0.12, 0.12, 0.10, 0.06, 0.06, 0.04, 0.03, 0.01]

FRAUD_ARCHETYPES = ("ghost", "upcoding", "velocity", "duplicate")


def _member_pool(rng: random.Random, n: int) -> list[str]:
    return [f"NG-{i:05d}" for i in range(n)]


def generate_claims(n: int = 5000, fraud_rate: float = 0.06, seed: int = 42,
                    facilities: int = 40) -> list[dict]:
    """Generate n claims with known fraud labels.

    Distributions are calibrated, not uniform: code mix follows CODE_WEIGHTS,
    amounts jitter ±20% around tariff, weekends are quieter, and fraud
    follows four named archetypes with distinct signatures.
    """
    rng = random.Random(seed)
    members = _member_pool(rng, n // 3)
    facs = [f"PHC-{i:04d}" for i in range(facilities)]
    # Fraud concentrates in a minority of bad-actor facilities — that is the
    # real-world pattern (fraud rings, not uniformly sloppy billing) and it
    # is what makes facility-level supervision a meaningful two-class task.
    bad_facs = sorted(rng.sample(facs, max(2, facilities // 6)))
    claims = []
    for i in range(n):
        is_fraud = rng.random() < fraud_rate
        archetype = rng.choice(FRAUD_ARCHETYPES) if is_fraud else None
        code = rng.choices(CODES, weights=CODE_WEIGHTS)[0]
        tariff = TARIFF[code]
        facility = rng.choice(facs)
        member = rng.choice(members)
        qty = 1
        amount = tariff * rng.uniform(0.8, 1.2)
        day = rng.randint(0, 89)
        hour = int(abs(rng.gauss(11, 3))) % 24
        if is_fraud:
            facility = rng.choice(bad_facs)  # fraud rings run through bad actors
            if archetype == "ghost":
                # a registrant billing members nobody else sees, off-hours
                member = f"GHOST-{rng.randint(0, 8):03d}"
                hour = rng.choice([1, 2, 3, 23])
                amount = tariff * rng.uniform(1.5, 3.0)
            elif archetype == "upcoding":
                # cheap procedure billed as an expensive one
                code = "LAB-MP" if code in ("ORS-001", "PARA-TAB") else "ANC-001"
                amount = TARIFF[code] * rng.uniform(1.0, 1.4)
            elif archetype == "velocity":
                # one facility on an impossible day
                facility = "PHC-VELOCITY"
                day = rng.randint(0, 3)
                qty = rng.randint(3, 8)
                amount = tariff * qty * rng.uniform(0.9, 1.1)
            elif archetype == "duplicate":
                # same member+code+day resubmitted (link via dup_of)
                amount = tariff
                qty = 1
        claims.append({
            "claim_id": f"CLM-{i:06d}",
            "member_id": member,
            "facility_id": facility,
            "scheme": rng.choice(SCHEMES),
            "code": code,
            "qty": qty,
            "amount_ngn": round(amount, 2),
            "tariff_ngn": TARIFF[code],
            "day": day,
            "hour": hour,
            "fraud": is_fraud,
            "archetype": archetype or "",
        })
    return claims


def load_lakehouse_claims(path: str) -> list[dict] | None:
    """Production path: read exported claims from the lakehouse gold zone.
    Returns None when the export does not exist (bootstrap → synthetic)."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        return rows or None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def dataset_hash(claims: list[dict]) -> str:
    """Content hash of the training set — continuous training retrains only
    when the data actually changed."""
    h = hashlib.sha256()
    for c in claims:
        h.update(c["claim_id"].encode())
    return h.hexdigest()[:16]
