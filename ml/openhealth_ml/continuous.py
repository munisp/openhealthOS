"""Continuous training (ADR-0014) — retrain when the data changes,
promote only when the challenger earns it.

Loop (each tick):
1. Read the lakehouse gold export (production claims) — fall back to
   calibrated synthetic only when no export exists (bootstrap).
2. Hash the dataset. If it matches the champion's card data_hash, do
   nothing: retraining on unchanged data only burns CPU.
3. Retrain on the new data, register the challenger.
4. Promote only if the challenger's holdout AUC beats the champion's.
   The champion is never replaced by a worse model, ever.

This is the honest version of "continuous training": scheduled (cron or
the loop below), data-driven, and gated on a metric — not a demo that
retrains on a timer and promotes unconditionally.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from . import data as D
from . import registry as R
from .train import train_claim_gcn, train_fraud_mlp

TRAINERS = {"fraud-mlp": train_fraud_mlp, "claim-gcn": train_claim_gcn}
DEFAULT_REGISTRY = os.path.join(os.path.dirname(__file__), "..", "registry")


def tick(registry: str, gold: str, model: str, epochs: int,
         bootstrap_claims: int = 5000) -> dict:
    claims = D.load_lakehouse_claims(gold)
    source = "lakehouse"
    if claims is None:
        claims = D.generate_claims(bootstrap_claims)
        source = "synthetic-calibrated"
    data_hash = D.dataset_hash(claims)

    champ_meta = R.load_champion(registry, model)
    if champ_meta and champ_meta["card"].get("data_hash") == data_hash:
        return {"model": model, "action": "skip",
                "reason": "data unchanged", "data_hash": data_hash}

    result = TRAINERS[model](claims, epochs=epochs)
    card = {"data_hash": data_hash, "n_claims": len(claims),
            "fraud_rate": round(sum(1 for c in claims if c["fraud"]) / len(claims), 4),
            "source": source}
    v = R.register(registry, model, result["weights"], result["metrics"], card)

    beat = True
    if champ_meta:
        beat = (result["metrics"]["holdout_auc_roc"]
                > champ_meta["metrics"]["holdout_auc_roc"])
    if beat:
        R.promote(registry, model, v)
    return {"model": model, "action": "promoted" if beat else "registered-not-promoted",
            "version": v, "data_hash": data_hash, "source": source,
            **result["metrics"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--gold", default="lakehouse/gold/claims.jsonl")
    ap.add_argument("--model", choices=sorted(TRAINERS), default="fraud-mlp")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--interval", type=int, default=3600,
                    help="seconds between ticks when looping")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    while True:
        print(json.dumps(tick(args.registry, args.gold, args.model,
                              args.epochs), indent=2), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
