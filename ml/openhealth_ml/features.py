"""Feature engineering (ADR-0014) — claims → tensors.

The tabular features are chosen to catch the four fraud archetypes:
amount deviation (upcoding), rarity/off-hours (ghost), facility-day volume
(velocity), member-day repeats (duplicate). The graph features wire
facilities, members and providers into the bipartite graph the GCN reads —
fraud rings live in shared structure, not single rows.
"""
from __future__ import annotations

from collections import defaultdict

FEATURES = [
    "amount_ratio",      # amount / tariff — upcoding signature
    "qty",
    "hour_sin",          # off-hours cyclic
    "hour_cos",
    "weekend",
    "facility_day_vol",  # claims by this facility on this day (velocity)
    "member_day_count",  # same member, same day (duplicate signature)
    "member_30d_count",  # member utilisation
    "facility_30d_count",
    "code_freq",         # how common this code is overall
]
NUM_FEATURES = len(FEATURES)

import math


def build_features(claims: list[dict]) -> list[list[float]]:
    fac_day = defaultdict(int)
    mem_day = defaultdict(int)
    mem_30 = defaultdict(int)
    fac_30 = defaultdict(int)
    code_freq = defaultdict(int)
    for c in claims:
        fac_day[(c["facility_id"], c["day"])] += 1
        mem_day[(c["member_id"], c["day"])] += 1
        mem_30[c["member_id"]] += 1
        fac_30[c["facility_id"]] += 1
        code_freq[c["code"]] += 1
    n = max(1, len(claims))
    rows = []
    for c in claims:
        hour = c["hour"]
        rows.append([
            c["amount_ngn"] / max(1.0, c["tariff_ngn"]),
            float(c["qty"]),
            math.sin(2 * math.pi * hour / 24),
            math.cos(2 * math.pi * hour / 24),
            1.0 if c["day"] % 7 in (5, 6) else 0.0,
            math.log1p(fac_day[(c["facility_id"], c["day"])]),
            math.log1p(mem_day[(c["member_id"], c["day"])]),
            math.log1p(mem_30[c["member_id"]]),
            math.log1p(fac_30[c["facility_id"]]),
            code_freq[c["code"]] / n,
        ])
    return rows


def labels(claims: list[dict]) -> list[int]:
    return [1 if c["fraud"] else 0 for c in claims]


# ------------------------------------------------------------------ graph

def build_graph(claims: list[dict]) -> dict:
    """Bipartite facility↔member graph for the GCN.

    Nodes: facilities first, then members. Edges: a claim links its
    facility and member. Node labels: a facility is positive if any of its
    claims are fraudulent (facility-level supervision — the question a
    payer asks is 'which facility do I audit').
    """
    fac_ids = sorted({c["facility_id"] for c in claims})
    mem_ids = sorted({c["member_id"] for c in claims})
    fac_ix = {f: i for i, f in enumerate(fac_ids)}
    mem_ix = {m: len(fac_ids) + i for i, m in enumerate(mem_ids)}
    n = len(fac_ids) + len(mem_ids)
    adj = [[0.0] * n for _ in range(n)]
    fac_fraud = defaultdict(bool)
    # Node features are STRUCTURAL ONLY — never derived from fraud labels.
    # (A "fraud rate so far" feature would be the label in disguise: target
    # leakage, and the model would learn nothing that transfers.)
    #   facility: [1, log claims, log distinct members]
    #   member:   [0, log claims, log distinct facilities]
    fac_claims = defaultdict(int)
    mem_claims = defaultdict(int)
    fac_members = defaultdict(set)
    mem_facs = defaultdict(set)
    for c in claims:
        fi, mi = fac_ix[c["facility_id"]], mem_ix[c["member_id"]]
        adj[fi][mi] += 1.0
        adj[mi][fi] += 1.0
        fac_claims[c["facility_id"]] += 1
        mem_claims[c["member_id"]] += 1
        fac_members[c["facility_id"]].add(c["member_id"])
        mem_facs[c["member_id"]].add(c["facility_id"])
        if c["fraud"]:
            fac_fraud[c["facility_id"]] = True
    import math as _m
    feats = []
    for f in fac_ids:
        feats.append([1.0, _m.log1p(fac_claims[f]),
                      _m.log1p(len(fac_members[f]))])
    for m in mem_ids:
        feats.append([0.0, _m.log1p(mem_claims[m]),
                      _m.log1p(len(mem_facs[m]))])
    y = [1 if fac_fraud[f] else 0 for f in fac_ids] + [0] * len(mem_ids)
    return {"adj": adj, "x": feats, "y": y, "n_fac": len(fac_ids),
            "facility_ids": fac_ids}
