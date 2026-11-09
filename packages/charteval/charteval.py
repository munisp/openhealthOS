"""charteval.py — scored harness for clinical-LLM features (ADR-0045).

ADR-0045's rule: any clinical-LLM feature (summarisation, evidence
retrieval, imaging pre-reads) ships with a Synthetic Hospital score in
its PR — run BEFORE shipping, not after an incident. This module is the
scoring half of that doctrine, faithful to the benchmark's conventions:

  - SEVERITY-WEIGHTED F1 for problem lists: missing the septic shock
    costs more than missing the acne. Weights are data, not vibes.
  - CHART-NEUTRAL scoring: the reference is the ground truth, but the
    chart may document MORE than the reference covers. A prediction
    present in the chart but absent from the reference is NEITHER
    credited NOR penalised; a prediction in neither is a hallucination
    and counts. Scoring what the chart never said as wrong is how
    benchmarks punish correct reading.
  - PATIENT-LEVEL SPLITS: encounters of one patient never straddle a
    split — leakage by encounter is leakage.
  - DENOMINATORS OR IT DIDN'T HAPPEN: every score carries n. A rate
    without n is marketing.
  - FLOORS ARE STATED: ADR-0045's rule — below the physician mean is a
    conversation, not a launch. The floor is an argument, not a
    hardcoded constant: deployments declare theirs.
"""

from __future__ import annotations

import hashlib
import math

VERSION = "charteval@1.0.0"


class ChartEvalError(Exception):
    pass


def severity_weighted_f1(reference, prediction, *, chart=None,
                         chart_neutral=True):
    """reference: {item: weight}. prediction: iterable of items.
    chart: set of items documented in the chart (for chart-neutral
    scoring of extra predictions)."""
    if not reference:
        raise ChartEvalError("empty reference — nothing to score against")
    pred = list(prediction)
    ref_w = sum(reference.values())
    matched = [p for p in pred if p in reference]
    matched_w = sum(reference[p] for p in matched)
    recall = matched_w / ref_w
    if chart_neutral and chart is not None:
        # predictions documented in the chart but absent from the
        # reference are ignored; hallucinations (in neither) count
        penalised = [p for p in pred
                     if p not in reference and p not in chart]
    else:
        penalised = [p for p in pred if p not in reference]
    # penalised weight: hallucinations cost the mean reference weight —
    # declared, not arbitrary: an unstated penalty is a rigged exam
    penalty_w = (ref_w / len(reference)) * len(penalised)
    denom = matched_w + penalty_w
    precision = matched_w / denom if denom else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def finding_f1(reference, predicted):
    """Plain F1 over findings (reference: set, predicted: iterable)."""
    if not reference:
        raise ChartEvalError("empty reference")
    pred = set(predicted)
    tp = len(set(reference) & pred)
    if not pred:
        return 0.0
    p, r = tp / len(pred), tp / len(reference)
    return 0.0 if p + r == 0 else 2 * p * r / (p + r)


def precision_at_k(ranked, relevant, k=5):
    if k <= 0:
        raise ChartEvalError("k must be positive")
    top = list(ranked)[:k]
    if not top:
        return 0.0
    return sum(1 for item in top if item in relevant) / len(top)


def ndcg_at_k(ranked, graded, k=5):
    """graded: {item: relevance 0..3}. DCG with log2 discount."""
    if k <= 0:
        raise ChartEvalError("k must be positive")

    def dcg(items):
        return sum((2 ** graded.get(item, 0) - 1) / math.log2(i + 2)
                   for i, item in enumerate(items))

    ideal = sorted(graded, key=lambda x: -graded[x])[:k]
    best = dcg(ideal)
    if best == 0:
        raise ChartEvalError("no relevant items — NDCG undefined")
    return dcg(list(ranked)[:k]) / best


def assign_split(patient_ref, *, splits=(("train", 0.6), ("public", 0.2),
                                         ("heldout", 0.2)), salt=""):
    """Deterministic patient-level split. The patient is the unit of
    leakage: every encounter inherits its patient's split, always."""
    total = sum(w for _, w in splits)
    if abs(total - 1.0) > 1e-9:
        raise ChartEvalError(f"split weights must sum to 1, got {total}")
    digest = hashlib.sha256(f"{salt}|{patient_ref}".encode()).digest()
    draw = int.from_bytes(digest[:8], "big") / 2 ** 64
    acc = 0.0
    for name, weight in splits:
        acc += weight
        if draw < acc:
            return name
    return splits[-1][0]


def floor_verdict(score, *, physician_mean, n):
    """ADR-0045: below the physician mean is a conversation, not a
    launch. The verdict states the gap; it never hides it."""
    if n <= 0:
        raise ChartEvalError("a score without n is marketing")
    gap = score - physician_mean
    verdict = "at-or-above-physician-mean" if gap >= 0 else \
        "below-physician-mean — conversation, not launch"
    return {"score": round(score, 4), "physician_mean": physician_mean,
            "gap": round(gap, 4), "n": n, "verdict": verdict}


def report(task, scores):
    """scores: list of per-instance floats. Mean with denominator."""
    if not scores:
        raise ChartEvalError("empty score list")
    return {"task": task, "n": len(scores),
            "mean": round(sum(scores) / len(scores), 4),
            "min": round(min(scores), 4), "max": round(max(scores), 4)}
