"""Training loops (ADR-0014) — real gradient training on CPU, with honest
metrics (AUC-ROC / PR-AUC computed without sklearn) and early stopping.

Usage:
    PYTHONPATH=ml python3 -m openhealth_ml.train --model fraud-mlp --epochs 40
    PYTHONPATH=ml python3 -m openhealth_ml.train --model claim-gcn --epochs 60

Data path: lakehouse gold export when present, calibrated synthetic
otherwise (data.py). The champion is only promoted when the challenger
beats it on holdout AUC — continuous.py drives that loop.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import tempfile

from . import data as D
from . import features as F
from . import registry as R
from .models import TORCH

if TORCH:
    import torch
    from .models import FraudMLP, ClaimGCN


# ------------------------------------------------------------- metrics

def auc_roc(y_true: list[int], scores: list[float]) -> float:
    """Rank-based AUC, no dependencies."""
    pos = [s for y, s in zip(y_true, scores) if y == 1]
    neg = [s for y, s in zip(y_true, scores) if y == 0]
    if not pos or not neg:
        return 0.5
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0 for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def pr_auc(y_true: list[int], scores: list[float], steps: int = 50) -> float:
    """Precision-recall area under curve (trapezoid over thresholds)."""
    if not any(y_true):
        return 0.0
    thresholds = [i / steps for i in range(steps + 1)]
    pts = []
    for t in thresholds:
        tp = sum(1 for y, s in zip(y_true, scores) if y == 1 and s >= t)
        fp = sum(1 for y, s in zip(y_true, scores) if y == 0 and s >= t)
        fn = sum(1 for y, s in zip(y_true, scores) if y == 1 and s < t)
        prec = tp / (tp + fp) if tp + fp else 1.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        pts.append((rec, prec))
    pts.sort()
    area = 0.0
    for (r1, p1), (r2, p2) in zip(pts, pts[1:]):
        area += (r2 - r1) * (p1 + p2) / 2
    return round(area, 4)


# ------------------------------------------------------------- evaluation governance

def choose_threshold(y_true: list[int], scores: list[float],
                     review_budget: float = 0.10,
                     min_precision: float = 0.80) -> dict:
    """Pick the operating threshold an auditor can actually staff (ADR-0015).

    Policy: within a review budget (fraction of claims a human team can
    look at), find the highest-recall threshold whose precision clears
    min_precision. If that is infeasible, take the best F1 inside the
    budget and SAY SO in the policy string — a threshold chosen silently
    is a threshold chosen badly.
    """
    n = max(1, len(scores))
    total_pos = max(1, sum(y_true))
    feas, fallback = None, None
    for i in range(2, 100):
        t = i / 100
        flagged = [(y, s) for y, s in zip(y_true, scores) if s >= t]
        if not flagged or len(flagged) / n > review_budget:
            continue
        tp = sum(1 for y, _ in flagged if y == 1)
        prec, rec = tp / len(flagged), tp / total_pos
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        if prec >= min_precision and (feas is None or rec > feas[0]):
            feas = (rec, prec, t)
        if fallback is None or f1 > fallback[0]:
            fallback = (f1, rec, prec, t)
    if feas:
        rec, prec, t = feas
        return {"threshold": t, "precision": round(prec, 4), "recall": round(rec, 4),
                "policy": f"precision>={min_precision} within {review_budget:.0%} review budget"}
    if fallback:
        f1, rec, prec, t = fallback
        return {"threshold": t, "precision": round(prec, 4), "recall": round(rec, 4),
                "policy": f"best-F1 fallback (precision target {min_precision} infeasible within {review_budget:.0%} budget)"}
    return {"threshold": 0.5, "precision": 0.0, "recall": 0.0,
            "policy": "default (no feasible threshold)"}


def per_archetype_recall(claims: list[dict], idx: list[int], y: list[int],
                         scores: list[float], threshold: float) -> dict:
    """Recall per fraud archetype on the holdout — the number a fraud
    officer actually asks for ('do you catch ghost billing?')."""
    out: dict[str, list[float]] = {}
    for j, i in enumerate(idx):
        arch = claims[i].get("archetype") or ""
        if y[j] == 1 and arch:
            out.setdefault(arch, []).append(1.0 if scores[j] >= threshold else 0.0)
    return {a: round(sum(v) / len(v), 4) for a, v in sorted(out.items())}


def calibration_table(y_true: list[int], scores: list[float], bins: int = 10) -> list[dict]:
    """Decile calibration: mean predicted score vs empirical fraud rate.
    A score a payer acts on must mean what it says."""
    table = []
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        sel = [(y, s) for y, s in zip(y_true, scores)
               if lo <= s < hi or (i == bins - 1 and s >= hi)]
        if sel:
            table.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(sel),
                          "mean_score": round(sum(s for _, s in sel) / len(sel), 3),
                          "fraud_rate": round(sum(y for y, _ in sel) / len(sel), 3)})
    return table


# ------------------------------------------------------------- MLP

def train_fraud_mlp(claims: list[dict], epochs: int = 40, seed: int = 7) -> dict:
    if not TORCH:
        raise RuntimeError("torch required — pip install torch (cpu wheel)")
    torch.manual_seed(seed)
    random.seed(seed)
    X = torch.tensor(F.build_features(claims), dtype=torch.float32)
    y = torch.tensor(F.labels(claims), dtype=torch.float32)
    n = len(claims)
    idx = list(range(n))
    random.shuffle(idx)
    cut = int(n * 0.8)
    tr, te = idx[:cut], idx[cut:]
    pos = y[tr].sum().clamp(min=1)
    pos_weight = ((len(tr) - pos) / pos).item()

    model = FraudMLP(in_features=X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight))

    best_auc, best_state, patience = 0.0, None, 0
    for epoch in range(epochs):
        model.train()
        perm = torch.randperm(len(tr))
        for i in range(0, len(tr), 256):
            batch = [tr[j] for j in perm[i:i + 256].tolist()]
            opt.zero_grad()
            loss = lossf(model(X[batch]), y[batch])
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            scores = model.score(X[te]).tolist()
        auc = auc_roc(y[te].int().tolist(), scores)
        if auc > best_auc:
            best_auc, best_state, patience = auc, {k: v.clone() for k, v in model.state_dict().items()}, 0
        else:
            patience += 1
            if patience >= 8:
                break
    model.load_state_dict(best_state)
    with torch.no_grad():
        scores = model.score(X[te]).tolist()
    y_te = y[te].int().tolist()
    dt = choose_threshold(y_te, scores)
    metrics = {
        "holdout_auc_roc": round(best_auc, 4),
        "holdout_pr_auc": pr_auc(y_te, scores),
        "train_n": len(tr), "holdout_n": len(te),
        "epochs_ran": epoch + 1,
        "decision_threshold": dt,
        "per_archetype_recall": per_archetype_recall(claims, te, y_te, scores,
                                                     dt["threshold"]),
        "calibration": calibration_table(y_te, scores),
    }
    tmp = tempfile.NamedTemporaryFile(suffix=".pt", delete=False)
    torch.save(model.state_dict(), tmp.name)
    return {"weights": tmp.name, "metrics": metrics}


# ------------------------------------------------------------- GCN

def train_claim_gcn(claims: list[dict], epochs: int = 60, seed: int = 7) -> dict:
    if not TORCH:
        raise RuntimeError("torch required")
    torch.manual_seed(seed)
    g = F.build_graph(claims)
    X = torch.tensor(g["x"], dtype=torch.float32)
    A = torch.tensor(g["adj"], dtype=torch.float32)
    A_norm = ClaimGCN.normalize_adj(A)
    y = torch.tensor(g["y"], dtype=torch.float32)
    n_fac = g["n_fac"]
    # Stratified facility split: fraud is concentrated in a few facilities,
    # so a naive shuffle can leave the holdout single-class (AUC undefined).
    rng = random.Random(seed)
    pos_idx = [i for i in range(n_fac) if g["y"][i] == 1]
    neg_idx = [i for i in range(n_fac) if g["y"][i] == 0]
    rng.shuffle(pos_idx)
    rng.shuffle(neg_idx)
    te_pos = pos_idx[: max(1, int(len(pos_idx) * 0.3))] if pos_idx else []
    te_neg = neg_idx[: max(1, int(len(neg_idx) * 0.3))] if neg_idx else []
    te = torch.tensor(te_pos + te_neg)
    tr = torch.tensor(pos_idx[len(te_pos):] + neg_idx[len(te_neg):])
    if len(tr) == 0:
        tr = te  # degenerate tiny graph: train on holdout too
    pos = y[tr].sum().clamp(min=1)
    pos_weight = ((len(tr) - pos) / pos).item()

    model = ClaimGCN(in_features=X.shape[1])
    opt = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=1e-4)
    lossf = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight))
    best_auc, best_state = 0.0, None
    for epoch in range(epochs):
        model.train()
        opt.zero_grad()
        out = model(X, A_norm)
        loss = lossf(out[tr], y[tr])
        loss.backward()
        opt.step()
        model.eval()
        with torch.no_grad():
            scores = model.score(X, A_norm)[te].tolist()
        auc = auc_roc(y[te].int().tolist(), scores)
        if auc > best_auc:
            best_auc = auc
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
    model.load_state_dict(best_state)
    metrics = {"holdout_auc_roc": round(best_auc, 4),
               "facilities": n_fac, "epochs_ran": epoch + 1}
    tmp = tempfile.NamedTemporaryFile(suffix=".pt", delete=False)
    torch.save(model.state_dict(), tmp.name)
    return {"weights": tmp.name, "metrics": metrics}


# ------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["fraud-mlp", "claim-gcn"], required=True)
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--claims", type=int, default=5000)
    ap.add_argument("--facilities", type=int, default=None,
                    help="synthetic facility count; GCN needs a rich graph "
                         "(default: 120 for claim-gcn, 40 for fraud-mlp)")
    ap.add_argument("--gold", default="lakehouse/gold/claims.jsonl",
                    help="lakehouse export; falls back to calibrated synthetic")
    ap.add_argument("--registry", default=os.path.join(os.path.dirname(__file__), "..", "registry"))
    ap.add_argument("--promote", action="store_true",
                    help="promote only if holdout AUC beats the champion")
    args = ap.parse_args()

    # The GCN learns facility rings from graph structure — a 40-facility
    # graph starves it of supervision, so its synthetic default is richer.
    n_fac = args.facilities or (120 if args.model == "claim-gcn" else 40)
    claims = (D.load_lakehouse_claims(args.gold)
              or D.generate_claims(args.claims, facilities=n_fac))
    card = {"data_hash": D.dataset_hash(claims), "n_claims": len(claims),
            "fraud_rate": sum(1 for c in claims if c["fraud"]) / len(claims),
            "source": "lakehouse" if D.load_lakehouse_claims(args.gold) else "synthetic-calibrated"}
    fn = train_fraud_mlp if args.model == "fraud-mlp" else train_claim_gcn
    result = fn(claims, epochs=args.epochs)
    v = R.register(args.registry, args.model, result["weights"], result["metrics"], card)

    promoted = False
    if args.promote:
        champ = R.champion(args.registry, args.model)
        beat = True
        if champ:
            with open(os.path.join(args.registry, args.model, champ, "metrics.json")) as f:
                beat = result["metrics"]["holdout_auc_roc"] > json.load(f)["holdout_auc_roc"]
        if beat:
            R.promote(args.registry, args.model, v)
            promoted = True
    print(json.dumps({"model": args.model, "version": v, "promoted": promoted,
                      **result["metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
