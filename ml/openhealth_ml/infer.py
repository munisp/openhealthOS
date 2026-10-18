"""CPU inference (ADR-0014) — load the registry champion, score claims.

Design rules:
* CPU only. Weights load with map_location="cpu"; no CUDA is ever assumed.
  The state-plane nodes are commodity x86/ARM servers, not GPU boxes.
* Advisory only. A score becomes a PEND recommendation with reasons for a
  human adjudicator — never an auto-deny (ADR-0012 payer doctrine).
* Graceful without a champion: score_claims returns heuristic scores from
  the rules (frauddetect package stays authoritative) when no model is
  registered, so a fresh deployment degrades to rules, not errors.
"""
from __future__ import annotations

import os

from . import features as F
from . import registry as R
from .models import TORCH

if TORCH:
    import torch
    from .models import FraudMLP, ClaimGCN

DEFAULT_REGISTRY = os.path.join(os.path.dirname(__file__), "..", "registry")

# Above this score a claim is recommended for human review (PEND).
PEND_THRESHOLD = 0.5


class ChampionScorer:
    """Scores claims with the champion FraudMLP. CPU-only."""

    def __init__(self, registry: str = DEFAULT_REGISTRY):
        if not TORCH:
            raise RuntimeError("torch required — pip install torch (cpu wheel)")
        self.registry = registry
        self._loaded = None  # (version, model, metrics)

    def _load(self):
        meta = R.load_champion(self.registry, "fraud-mlp")
        if meta is None:
            return None
        if self._loaded and self._loaded[0] == meta["version"]:
            return self._loaded
        model = FraudMLP(in_features=F.NUM_FEATURES)
        state = torch.load(meta["weights_path"], map_location="cpu",
                           weights_only=True)
        model.load_state_dict(state)
        model.eval()
        self._loaded = (meta["version"], model, meta["metrics"])
        return self._loaded

    def champion_version(self) -> str | None:
        loaded = self._load()
        return loaded[0] if loaded else None

    def score_claims(self, claims: list[dict]) -> list[dict]:
        """Score each claim. Returns one dict per claim:
        {claim_id, score, action, model_version, archetype_hint}.
        Falls back to a rule-shaped heuristic when no champion exists."""
        loaded = self._load()
        rows = F.build_features(claims)
        if loaded is None:
            return [self._heuristic(c, r) for c, r in zip(claims, rows)]
        version, model, _ = loaded
        with torch.no_grad():
            probs = model.score(torch.tensor(rows, dtype=torch.float32)).tolist()
        out = []
        for c, p in zip(claims, probs):
            out.append({
                "claim_id": c["claim_id"],
                "score": round(float(p), 4),
                "action": "PEND" if p >= PEND_THRESHOLD else "PASS",
                "model_version": version,
                "archetype_hint": self._hint(c),
            })
        return out

    @staticmethod
    def _hint(c: dict) -> str:
        hints = []
        if c["amount_ngn"] > 1.5 * max(1.0, c["tariff_ngn"]):
            hints.append("amount>tariff")
        if c["hour"] in (0, 1, 2, 3, 23):
            hints.append("off-hours")
        if c["qty"] >= 3:
            hints.append("high-qty")
        return ",".join(hints)

    def _heuristic(self, c: dict, row: list[float]) -> dict:
        # amount_ratio and velocity dominate; monotone squash into (0,1).
        x = 0.35 * min(3.0, row[0]) + 0.25 * min(3.0, row[5]) \
            + (0.3 if c["hour"] in (0, 1, 2, 3, 23) else 0.0)
        p = min(0.99, x / 2.0)
        return {"claim_id": c["claim_id"], "score": round(p, 4),
                "action": "PEND" if p >= PEND_THRESHOLD else "PASS",
                "model_version": "heuristic-fallback",
                "archetype_hint": self._hint(c)}


class FacilityGNNScorer:
    """Scores facilities with the champion ClaimGCN over the
    facility↔member graph. CPU-only."""

    def __init__(self, registry: str = DEFAULT_REGISTRY):
        if not TORCH:
            raise RuntimeError("torch required")
        self.registry = registry

    def score_facilities(self, claims: list[dict]) -> list[dict]:
        meta = R.load_champion(self.registry, "claim-gcn")
        if meta is None:
            return []
        g = F.build_graph(claims)
        X = torch.tensor(g["x"], dtype=torch.float32)
        A = torch.tensor(g["adj"], dtype=torch.float32)
        A_norm = ClaimGCN.normalize_adj(A)
        model = ClaimGCN(in_features=X.shape[1])
        model.load_state_dict(torch.load(meta["weights_path"],
                                         map_location="cpu", weights_only=True))
        model.eval()
        with torch.no_grad():
            probs = model.score(X, A_norm)[: g["n_fac"]].tolist()
        return [{"facility_id": f, "score": round(float(p), 4),
                 "action": "AUDIT" if p >= PEND_THRESHOLD else "OK",
                 "model_version": meta["version"]}
                for f, p in sorted(zip(g["facility_ids"], probs),
                                   key=lambda t: -t[1])]


def main() -> int:
    import argparse
    import json
    from . import data as D

    ap = argparse.ArgumentParser()
    ap.add_argument("--registry", default=DEFAULT_REGISTRY)
    ap.add_argument("--gold", default="lakehouse/gold/claims.jsonl")
    ap.add_argument("--claims", type=int, default=200)
    ap.add_argument("--gnn", action="store_true", help="facility-level GNN")
    args = ap.parse_args()
    claims = D.load_lakehouse_claims(args.gold) or D.generate_claims(args.claims)
    if args.gnn:
        out = FacilityGNNScorer(args.registry).score_facilities(claims)
    else:
        out = ChampionScorer(args.registry).score_claims(claims)
    print(json.dumps(out[:20], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
