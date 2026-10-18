"""Champion/challenger A/B (ADR-0014).

Deterministic assignment: sha256(claim_id) decides the arm, so the same
claim always lands in the same arm regardless of which node scores it —
no coordination, no Redis, no clock skew. The challenger never decides
outcomes: both arms' scores are logged, the champion's action is the one
returned to the adjudicator, and the comparison report is what a human
uses to promote (or roll back) the challenger.
"""
from __future__ import annotations

import hashlib
import json


def assign_arm(claim_id: str, challenger_pct: int = 10) -> str:
    """'champion' or 'challenger' — stable per claim_id."""
    if not 0 <= challenger_pct <= 100:
        raise ValueError("challenger_pct must be 0..100")
    h = int(hashlib.sha256(claim_id.encode()).hexdigest(), 16) % 100
    return "challenger" if h < challenger_pct else "champion"


def compare(champion_rows: list[dict], challenger_rows: list[dict],
            labels: dict[str, int]) -> dict:
    """Side-by-side report. labels maps claim_id → 0/1 ground truth
    (from adjudication outcomes, when they exist)."""
    from .train import auc_roc

    def _metrics(rows):
        known = [(labels[r["claim_id"]], r["score"]) for r in rows
                 if r["claim_id"] in labels]
        if not known:
            return {"n": len(rows), "labelled": 0}
        y = [k[0] for k in known]
        s = [k[1] for k in known]
        pends = sum(1 for r in rows if r["action"] == "PEND")
        return {"n": len(rows), "labelled": len(known),
                "auc_roc": round(auc_roc(y, s), 4),
                "pend_rate": round(pends / len(rows), 4)}

    return {"champion": _metrics(champion_rows),
            "challenger": _metrics(challenger_rows)}


def main() -> int:
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--pct", type=int, default=10)
    ap.add_argument("claim_ids", nargs="*")
    args = ap.parse_args()
    ids = args.claim_ids or [l.strip() for l in sys.stdin if l.strip()]
    print(json.dumps({c: assign_arm(c, args.pct) for c in ids}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
