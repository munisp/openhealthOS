"""FraudFusion — calibrated multi-signal fraud scoring.

One claim (or facility-month) is looked at by many detectors at once:
the rule engine (packages/frauddetect), the GNN ring score (gnn.py),
duplicate-biometric hits, stock reconciliation divergence, MCMC posterior
rates (mcmc.py), tariff outliers, ... Each emits a 0..1 score. FraudFusion
combines them with a logistic model so the combined score is a calibrated
probability, and — just as important for an auditor — a per-signal
contribution breakdown so every flag can be explained in plain language.

Weights ship with sensible defaults and can be refit on labelled outcomes
with fit() (plain gradient descent, stdlib-only).
"""

import math

# Default weights: hand-set from domain reasoning; refit with fit() once
# labelled adjudication outcomes accumulate. Intercept keeps a lone weak
# signal below the review threshold.
DEFAULT_WEIGHTS = {
    "bias": -2.2,
    "rule_engine": 2.6,        # packages/frauddetect deterministic flags
    "gnn_ring": 1.8,           # neighbourhood collusion score
    "duplicate_biometric": 2.4,
    "stock_divergence": 1.6,
    "mcmc_rate": 1.4,          # facility-level posterior exceedance
    "tariff_outlier": 1.2,
}

REVIEW_THRESHOLD = 0.5
AUTO_PEND_THRESHOLD = 0.8


class Signal:
    def __init__(self, name, score, detail=""):
        if name not in DEFAULT_WEIGHTS:
            raise ValueError(f"unknown signal: {name}")
        if not 0.0 <= score <= 1.0:
            raise ValueError("signal scores must be in [0, 1]")
        self.name = name
        self.score = float(score)
        self.detail = detail


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fuse(signals, weights=None):
    """Combine signals -> {probability, verdict, contributions}.

    verdict: clear (<0.5) | review (0.5-0.8) | auto_pend (>=0.8)
    contributions: per-signal share of the logit, for auditor explainability.
    """
    w = weights or DEFAULT_WEIGHTS
    z = w["bias"]
    contribs = {}
    for s in signals:
        c = w[s.name] * s.score
        z += c
        contribs[s.name] = {"score": s.score, "logit_share": round(c, 4),
                            "detail": s.detail}
    p = _sigmoid(z)
    verdict = ("auto_pend" if p >= AUTO_PEND_THRESHOLD
               else "review" if p >= REVIEW_THRESHOLD else "clear")
    return {"probability": round(p, 4), "verdict": verdict,
            "contributions": contribs}


def fit(rows, epochs=400, lr=0.3, l2=1e-3, seed=7):
    """Refit weights on labelled rows.

    rows: [([Signal, ...], label)] with label in {0, 1}.
    Returns a new weights dict; the bias is retrained too.
    """
    import random
    random.seed(seed)
    names = [n for n in DEFAULT_WEIGHTS if n != "bias"]
    w = {n: 0.0 for n in names}
    b = 0.0
    data = [({s.name: s.score for s in sigs}, float(lab)) for sigs, lab in rows]
    m = float(len(data))
    for _ in range(epochs):
        gb, gw = 0.0, {n: 0.0 for n in names}
        for feats, lab in data:
            z = b + sum(w[n] * feats.get(n, 0.0) for n in names)
            err = _sigmoid(z) - lab
            gb += err
            for n in names:
                gw[n] += err * feats.get(n, 0.0)
        b -= lr * (gb / m)
        for n in names:
            w[n] -= lr * (gw[n] / m + l2 * w[n])
    out = {"bias": b}
    out.update(w)
    return out
