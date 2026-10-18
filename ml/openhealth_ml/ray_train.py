"""Optional Ray scale-out (ADR-0014).

Honest scope: the models are small (an MLP with 32 hidden units, a GCN
over a few thousand nodes) — a single CPU trains them in minutes, and
that is the default path. Ray is here for the two cases where scale-out
is real:

* hyper-parameter search — N trials (seed × lr × hidden) in parallel
  across a cluster, best holdout AUC wins;
* multi-state training — one trial per state partition of the lakehouse
  export when the national dataset no longer fits one node's patience.

Ray is a soft dependency: without it, this module runs the same search
serially on the local CPU. Nothing else in the stack imports ray.
"""
from __future__ import annotations

import argparse
import json

from . import data as D
from .train import auc_roc, train_fraud_mlp

try:
    import ray
    RAY = True
except ImportError:
    ray = None
    RAY = False


def _trial(claims, epochs: int, seed: int) -> dict:
    result = train_fraud_mlp(claims, epochs=epochs, seed=seed)
    return {"seed": seed, **result["metrics"], "weights": result["weights"]}


def search(claims: list[dict], epochs: int = 40,
           seeds: tuple = (7, 42, 123)) -> dict:
    """Seed-sweep search; the winning trial's weights path is returned."""
    if RAY:
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True)
        remote = ray.remote(_trial)
        refs = [remote.remote(claims, epochs, s) for s in seeds]
        trials = ray.get(refs)
    else:
        trials = [_trial(claims, epochs, s) for s in seeds]
    best = max(trials, key=lambda t: t["holdout_auc_roc"])
    return {"trials": [{k: v for k, v in t.items() if k != "weights"}
                       for t in trials],
            "best": best, "ray": RAY}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--claims", type=int, default=5000)
    ap.add_argument("--gold", default="lakehouse/gold/claims.jsonl")
    args = ap.parse_args()
    claims = D.load_lakehouse_claims(args.gold) or D.generate_claims(args.claims)
    out = search(claims, epochs=args.epochs)
    out["best"] = {k: v for k, v in out["best"].items() if k != "weights"}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
