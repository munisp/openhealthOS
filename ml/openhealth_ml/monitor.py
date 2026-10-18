"""Model monitoring (ADR-0014) — drift detection and score tracking.

Pure stdlib: this runs on the state plane next to the event consumers,
where torch is not installed. It reads feature rows and score rows, never
model weights.

* PSI (population stability index) per feature between the training
  reference window and the current window. PSI > 0.25 = significant drift
  (retrain now), 0.1–0.25 = moderate (watch), < 0.1 = stable.
* Score tracking: mean/max/PEND-rate per window, so a silently degrading
  champion shows up as a distribution shift before accuracy data exists.
"""
from __future__ import annotations

import json
import math

from . import features as F

PSI_MODERATE = 0.1
PSI_SIGNIFICANT = 0.25


def psi(reference: list[float], current: list[float], bins: int = 10) -> float:
    """Population stability index between two samples of one feature."""
    if not reference or not current:
        return 0.0
    lo = min(min(reference), min(current))
    hi = max(max(reference), max(current))
    if hi == lo:
        return 0.0
    width = (hi - lo) / bins
    ref_counts = [0] * bins
    cur_counts = [0] * bins
    for v in reference:
        ref_counts[min(bins - 1, int((v - lo) / width))] += 1
    for v in current:
        cur_counts[min(bins - 1, int((v - lo) / width))] += 1
    n_ref, n_cur = len(reference), len(current)
    total = 0.0
    for rc, cc in zip(ref_counts, cur_counts):
        # 0.5 pseudo-count keeps log() finite on empty bins.
        pr = (rc + 0.5) / (n_ref + 0.5 * bins)
        pc = (cc + 0.5) / (n_cur + 0.5 * bins)
        total += (pr - pc) * math.log(pr / pc)
    return round(total, 4)


def drift_report(reference_claims: list[dict],
                 current_claims: list[dict]) -> dict:
    """Per-feature PSI + an overall verdict."""
    ref = F.build_features(reference_claims)
    cur = F.build_features(current_claims)
    per_feature = {}
    worst = 0.0
    for i, name in enumerate(F.FEATURES):
        p = psi([r[i] for r in ref], [r[i] for r in cur])
        per_feature[name] = p
        worst = max(worst, p)
    verdict = ("significant" if worst >= PSI_SIGNIFICANT
               else "moderate" if worst >= PSI_MODERATE else "stable")
    return {"verdict": verdict, "worst_psi": worst,
            "per_feature": per_feature,
            "reference_n": len(reference_claims),
            "current_n": len(current_claims)}


def score_window(scored: list[dict]) -> dict:
    """Summarise one window of ChampionScorer output."""
    if not scored:
        return {"n": 0}
    scores = [s["score"] for s in scored]
    pends = sum(1 for s in scored if s["action"] == "PEND")
    return {"n": len(scored),
            "mean_score": round(sum(scores) / len(scores), 4),
            "max_score": round(max(scores), 4),
            "pend_rate": round(pends / len(scored), 4)}


def main() -> int:
    import argparse
    from . import data as D

    ap = argparse.ArgumentParser()
    ap.add_argument("--reference-gold", default="lakehouse/gold/claims.jsonl")
    ap.add_argument("--current-gold", default="lakehouse/gold/claims_recent.jsonl")
    args = ap.parse_args()
    ref = D.load_lakehouse_claims(args.reference_gold)
    cur = D.load_lakehouse_claims(args.current_gold)
    if ref is None or cur is None:
        print(json.dumps({"verdict": "no-data",
                          "detail": "lakehouse exports not found"}))
        return 0
    print(json.dumps(drift_report(ref, cur), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
