"""Validation harness (ADR-0015) — score the champion against ANY
adjudicated claims export and emit a full governance report.

This is the answer to "never validated against real fraud cases": the
moment an adjudicated export exists (claims with confirmed fraud labels
from the review lane), `validate.py` produces the evidence pack — AUC,
PR-AUC, per-archetype recall at the operating threshold, calibration,
and the confusion matrix — and fails loudly (exit 1) if the champion
drops below the floor. CI runs it against the calibrated synthetic
holdout so a bad champion can never be committed silently.

    PYTHONPATH=ml python3 -m openhealth_ml.validate \
        --gold lakehouse/gold/claims_adjudicated.jsonl [--min-auc 0.75]
"""
from __future__ import annotations

import argparse
import json
import os

from . import data as D
from . import registry as R
from .infer import ChampionScorer, DEFAULT_REGISTRY
from .train import (auc_roc, calibration_table, per_archetype_recall,
                    pr_auc)

FLOOR_AUC = 0.75  # below this a champion is not a champion


def validate(claims: list[dict], registry: str = DEFAULT_REGISTRY,
             min_auc: float = FLOOR_AUC) -> dict:
    meta = R.load_champion(registry, "fraud-mlp")
    if meta is None:
        return {"ok": False, "error": "no champion registered"}
    scorer = ChampionScorer(registry)
    out = scorer.score_claims(claims)
    scores = [o["score"] for o in out]
    y = [1 if c["fraud"] else 0 for c in claims]
    threshold = out[0].get("threshold", 0.5) if out else 0.5

    tp = sum(1 for yi, s in zip(y, scores) if yi == 1 and s >= threshold)
    fp = sum(1 for yi, s in zip(y, scores) if yi == 0 and s >= threshold)
    fn = sum(1 for yi, s in zip(y, scores) if yi == 1 and s < threshold)
    tn = sum(1 for yi, s in zip(y, scores) if yi == 0 and s < threshold)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0

    auc = round(auc_roc(y, scores), 4)
    report = {
        "model_version": meta["version"],
        "champion_trained_on": meta["card"].get("source", "unknown"),
        "validated_on_n": len(claims),
        "validated_fraud_rate": round(sum(y) / max(1, len(y)), 4),
        "auc_roc": auc,
        "pr_auc": pr_auc(y, scores),
        "operating_threshold": threshold,
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "per_archetype_recall": per_archetype_recall(
            claims, list(range(len(claims))), y, scores, threshold),
        "calibration": calibration_table(y, scores),
        "ok": auc >= min_auc,
        "floor_auc": min_auc,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default=None,
                    help="adjudicated claims export; synthetic holdout when absent")
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--min-auc", type=float, default=FLOOR_AUC)
    ap.add_argument("--claims", type=int, default=4000,
                    help="synthetic holdout size when --gold absent")
    args = ap.parse_args()
    claims = D.load_lakehouse_claims(args.gold) if args.gold else None
    source = "adjudicated-export"
    if claims is None:
        claims = D.generate_claims(args.claims, seed=4242)  # unseen seed — honest holdout
        source = "synthetic-holdout"
    report = validate(claims, args.registry, min_auc=args.min_auc)
    report["data_source"] = source
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
