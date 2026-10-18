"""Glue between the claims stream and the three fraud-ML components.

Input is a plain list of claim dicts (as exported by the adjudicator /
claims ledger), each with at least:
    {"id", "facility_id", "provider_id", "patient_id",
     "flagged": bool, "tariff_z": float, "duplicate_biometric": bool}

Output is, per facility and per claim, fused, explainable verdicts — the
exact artefact an ADR-0018 anti-collusion review board needs.
"""

import gnn
import fraudfusion
import mcmc


def build_graph(claims):
    """Facility–provider–patient graph; edge weight = claim volume on that
    relationship. Node features: [flag_rate, mean tariff_z, dup-biometric
    hit rate] computed from the claims touching each node."""
    nodes, feats, edges = [], {}, {}
    stats = {}

    def stat(nid):
        return stats.setdefault(nid, {"n": 0, "flags": 0, "tz": 0.0, "dups": 0})

    for c in claims:
        f, p, pt = c["facility_id"], c["provider_id"], c["patient_id"]
        for nid in (f, p, pt):
            if nid not in stats:
                stat(nid)
                nodes.append(nid)
        for pair in ((f, p), (f, pt), (p, pt)):
            edges[pair] = edges.get(pair, 0.0) + 1.0
        s = stat(f)
        s["n"] += 1
        s["flags"] += 1 if c.get("flagged") else 0
        s["tz"] += float(c.get("tariff_z", 0.0))
        s["dups"] += 1 if c.get("duplicate_biometric") else 0
        # providers/patients inherit the claim-level flags as soft signals
        for nid in (p, pt):
            sp = stat(nid)
            sp["n"] += 1
            sp["flags"] += 1 if c.get("flagged") else 0
            sp["dups"] += 1 if c.get("duplicate_biometric") else 0

    for nid in nodes:
        s = stats[nid]
        n = max(s["n"], 1)
        feats[nid] = [s["flags"] / n, s["tz"] / n, s["dups"] / n]
    g = gnn.Graph(nodes, [feats[nid] for nid in nodes])
    for (a, b), w in edges.items():
        g.add_edge(a, b, w)
    return g


def ring_scores(claims, labelled_verdicts=None, use_gcn=True):
    """labelled_verdicts: {node_id: 0|1} from past adjudications. Without
    labels (cold start) falls back to label propagation seeded by each
    node's own flag rate."""
    g = build_graph(claims)
    if labelled_verdicts and use_gcn and len(set(labelled_verdicts.values())) == 2:
        model = gnn.GCN(g, hidden=8, seed=42, epochs=250, lr=0.25)
        return model.fit(labelled_verdicts).ring_scores()
    stats = {}
    for c in claims:
        for nid in (c["facility_id"], c["provider_id"]):
            s = stats.setdefault(nid, [0, 0])
            s[0] += 1
            s[1] += 1 if c.get("flagged") else 0
    seeds = {nid: 1 if (s[1] / s[0]) > 0.3 else 0 for nid, s in stats.items()}
    return gnn.label_propagation(g, seeds)


def facility_observations(claims):
    obs = {}
    for c in claims:
        o = obs.setdefault(c["facility_id"], {"claims": 0, "flagged": 0})
        o["claims"] += 1
        o["flagged"] += 1 if c.get("flagged") else 0
    return obs


def assess(claims, labelled_verdicts=None, rule_scores=None,
           draws=2000, burn=1000, seed=42):
    """End-to-end: GNN ring scores + MCMC facility rates + fused verdicts.

    rule_scores: optional {claim_id: 0..1} from packages/frauddetect.
    Returns {"rings": {...}, "facilities": {...}, "claims": {...}}.
    """
    rings = ring_scores(claims, labelled_verdicts)
    fac_report = mcmc.facility_report(facility_observations(claims),
                                      draws=draws, burn=burn, seed=seed)
    fac_idx = {r["facility"]: r for r in fac_report["facilities"]}

    claim_verdicts = {}
    for c in claims:
        sigs = [
            fraudfusion.Signal(
                "gnn_ring", rings.get(c["facility_id"], 0.0),
                "facility neighbourhood score"),
            fraudfusion.Signal(
                "mcmc_rate",
                min(fac_idx[c["facility_id"]]["posterior_mean"] / 0.3, 1.0),
                "posterior flag rate vs 30% ceiling"),
            fraudfusion.Signal(
                "tariff_outlier",
                min(abs(float(c.get("tariff_z", 0.0))) / 4.0, 1.0)),
            fraudfusion.Signal(
                "duplicate_biometric",
                1.0 if c.get("duplicate_biometric") else 0.0),
        ]
        if rule_scores and c["id"] in rule_scores:
            sigs.append(fraudfusion.Signal("rule_engine",
                                           rule_scores[c["id"]]))
        claim_verdicts[c["id"]] = fraudfusion.fuse(sigs)
    return {"rings": rings, "facilities": fac_report, "claims": claim_verdicts}
