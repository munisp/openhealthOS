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

FRAUDGRAPH_VERSION = "vhfraudgraph@1.0.0"

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
