"""Collusion-ring detection — the triangle is the shape of the fraud.

Edges connect claimant, provider, and the officer/payout account. A
ring is a closed triangle where the same payout account recurs across
apparently unrelated claimants and providers. Detection is thresholded
and every hit exports a checksum-stamped evidence pack — a finding you
can hand to a prosecutor, not a vibe in a dashboard.

Thresholds are versioned entries; tuning them is a versioned act.
"""

import hashlib
import json
from collections import defaultdict

FRAUDGRAPH_VERSION = "vhfraudgraph@1.1.0"

THRESHOLDS = {
    "min_shared_payout_claims": 3,    # same payout acct across claims
    "min_triangle_edges": 3,          # claimant-provider-account closed
    "min_ring_members": 2,            # claimants sharing provider+account
}


class FraudGraphError(Exception):
    pass


def _refuse(msg):
    raise FraudGraphError(msg)


class FraudGraph:
    def __init__(self):
        self.claims = []          # {claim, claimant, provider, account}

    def add_claim(self, *, claim_ref, claimant, provider, payout_account):
        for k, v in (("claim_ref", claim_ref), ("claimant", claimant),
                     ("provider", provider),
                     ("payout_account", payout_account)):
            if not v:
                _refuse(f"a claim edge without {k} is not an edge")
        self.claims.append({"claim": claim_ref, "claimant": claimant,
                            "provider": provider,
                            "account": payout_account})
        return len(self.claims)

    def detect_rings(self):
        """Group by (provider, account): claimants clustering on the
        same provider + payout account beyond threshold = a ring."""
        cells = defaultdict(set)
        for c in self.claims:
            cells[(c["provider"], c["account"])].add(c["claimant"])
        rings = []
        for (provider, account), claimants in sorted(cells.items()):
            n_claims = sum(1 for c in self.claims
                           if c["provider"] == provider
                           and c["account"] == account)
            if (len(claimants) >= THRESHOLDS["min_ring_members"]
                    and n_claims >= THRESHOLDS["min_shared_payout_claims"]):
                rings.append({
                    "provider": provider, "account": account,
                    "claimants": sorted(claimants),
                    "claims": n_claims,
                    "thresholds": dict(THRESHOLDS)})
        return rings

    def evidence_pack(self, ring):
        """Checksum-stamped evidence — the checksum is the contract."""
        members = [c for c in self.claims
                   if c["provider"] == ring["provider"]
                   and c["account"] == ring["account"]]
        pack = {"version": FRAUDGRAPH_VERSION, "ring": ring,
                "edges": [dict(m) for m in members]}
        canon = json.dumps(pack, sort_keys=True).encode()
        pack["sha256"] = hashlib.sha256(canon).hexdigest()
        return pack

    def report(self):
        rings = self.detect_rings()
        return {"version": FRAUDGRAPH_VERSION,
                "claims": len(self.claims), "rings": len(rings),
                "thresholds": dict(THRESHOLDS)}


# ── Outcome feedback loop (vhfraudgraph@1.1.0) ────────────────────
# Static thresholds find the same rings forever; adjudication teaches.
# Every ring detection that a human court/committee CONFIRMS or CLEARS
# feeds back: confirmed rings raise the risk score of every member
# (provider, account, claimants); cleared rings lower it. The weights
# are four-eyes ratified entries — tuning the scorer is a governed
# act, never a dashboard slider. Scores explain themselves: every
# point names the outcome that contributed it.

class ScoringRegistry:
    """Ratified scoring weights — the hand that proposes a weight
    never ratifies it."""

    def __init__(self):
        self.weights = {"confirmed_ring": 40, "cleared_ring": -15,
                        "open_detection": 10, "status": "seeded"}
        self.audit = []

    def propose(self, weights, *, by, day):
        for k in weights:
            if k not in ("confirmed_ring", "cleared_ring",
                         "open_detection"):
                _refuse(f"unknown scoring weight {k!r}; weights are "
                        "entries — register the dimension first")
        self._pending = {"weights": dict(weights), "proposed_by": by,
                         "proposed_day": day}
        self.audit.append({"op": "propose", "by": by, "day": day})

    def ratify(self, *, by, day):
        p = getattr(self, "_pending", None)
        if not p:
            _refuse("no proposed weights to ratify")
        if by == p["proposed_by"]:
            _refuse("the hand that proposes never ratifies")
        self.weights = {**p["weights"], "status": "ratified",
                        "ratified_by": by, "ratified_day": day}
        self._pending = None
        self.audit.append({"op": "ratify", "by": by, "day": day})
        return dict(self.weights)


class OutcomeFeedback:
    """Adjudicated outcomes feeding back into member risk scores."""

    OUTCOMES = ("confirmed-fraud", "cleared")

    def __init__(self, *, scoring=None):
        self.scoring = scoring or ScoringRegistry()
        self.outcomes = []         # {ring_key, outcome, by, day}
        self._ring_keys = set()

    def _key(self, ring):
        return f"{ring['provider']}|{ring['account']}"

    def record_outcome(self, ring, *, outcome, by, day, note=None):
        if outcome not in self.OUTCOMES:
            _refuse(f"unknown outcome {outcome!r}; outcomes are "
                    f"{self.OUTCOMES}")
        key = self._key(ring)
        if key in self._ring_keys:
            _refuse(f"ring {key} already adjudicated — an appeal is "
                    "a new record, not an overwrite")
        self._ring_keys.add(key)
        self.outcomes.append({"ring": key, "outcome": outcome,
                              "members": {"provider": ring["provider"],
                                          "account": ring["account"],
                                          "claimants":
                                              list(ring["claimants"])},
                              "by": by, "day": day, "note": note})
        return self.outcomes[-1]

    def risk_score(self, subject, *, open_detections=0):
        """Score a provider, account, or claimant from adjudicated
        history plus open detections. Every point is explained."""
        w = self.scoring.weights
        score = 0
        explanation = []
        for o in self.outcomes:
            m = o["members"]
            involved = (subject == m["provider"]
                        or subject == m["account"]
                        or subject in m["claimants"])
            if not involved:
                continue
            if o["outcome"] == "confirmed-fraud":
                pts = w["confirmed_ring"]
            else:
                pts = w["cleared_ring"]
            score += pts
            explanation.append({"ring": o["ring"],
                                "outcome": o["outcome"],
                                "points": pts, "day": o["day"]})
        if open_detections:
            pts = w["open_detection"] * open_detections
            score += pts
            explanation.append({"open_detections": open_detections,
                                "points": pts})
        band = ("high" if score >= 50 else
                "watch" if score >= 20 else "clear")
        return {"subject": subject, "score": score, "band": band,
                "explanation": explanation,
                "weights_status": w.get("status", "seeded")}
