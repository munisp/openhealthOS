"""Fraud-ring explorer (ADR-0015) — the Neo4j-row answer, stdlib-only.

A fraud ring is not a row, it is a connected structure: facilities that
share members, members who only appear together, ghost identities reused
across "unrelated" clinics. A graph database (Neo4j) is the usual tool —
but its clustering licence is restrictive and the platform's doctrine is
never to embed one. The exploration job itself is a connected-components
walk over the claim graph, and that is stdlib Python:

  nodes  = facilities ∪ members
  edges  = a claim between them
  rings  = connected components with ≥2 facilities

Each ring reports what an investigator needs: member/facility counts,
claim volume, fraud volume (when labels exist), and the shared members
binding the ring together. Runs over the lakehouse gold export; feeds the
GCN's facility audit ranking (infer.FacilityGNNScorer) as a complementary,
explainable view.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

from . import data as D


def find_rings(claims: list[dict], min_facilities: int = 2,
               min_shared_members: int = 1, fraud_only: bool = False) -> list[dict]:
    """Connected components over the facility↔member bipartite graph.

    fraud_only=True builds edges from fraud-labelled claims only — on
    random utilisation data every facility shares members with every other
    (one giant component, no signal); restricting to suspicious edges is
    what makes the ring structure stand out. On unlabeled production data,
    leave it False and rank by shared-member density."""
    edge_claims = [c for c in claims if c.get("fraud")] if fraud_only else claims
    fac_members: dict[str, set[str]] = defaultdict(set)
    mem_facs: dict[str, set[str]] = defaultdict(set)
    claims_per: dict[str, int] = defaultdict(int)
    fraud_per: dict[str, int] = defaultdict(int)
    for c in edge_claims:
        f, m = c["facility_id"], c["member_id"]
        fac_members[f].add(m)
        mem_facs[m].add(f)
        claims_per[f] += 1
        if c.get("fraud"):
            fraud_per[f] += 1
    # Volume context always comes from the FULL claim set.
    all_claims_per: dict[str, int] = defaultdict(int)
    for c in claims:
        all_claims_per[c["facility_id"]] += 1

    # Union-find over facility+member nodes.
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find("F:" + a), find("M:" + b)
        if ra != rb:
            parent[ra] = rb

    for c in edge_claims:
        union(c["facility_id"], c["member_id"])

    comps: dict[str, dict] = defaultdict(lambda: {"facilities": set(), "members": set()})
    for f in fac_members:
        comps[find("F:" + f)]["facilities"].add(f)
    for m in mem_facs:
        comps[find("M:" + m)]["members"].add(m)

    rings = []
    for comp in comps.values():
        facs = sorted(comp["facilities"])
        if len(facs) < min_facilities:
            continue
        # Members binding the ring: seen at ≥2 of its facilities.
        shared = sorted(m for m in comp["members"]
                        if len(mem_facs[m] & set(facs)) >= 2)
        if len(shared) < min_shared_members:
            continue
        total_claims = sum(claims_per[f] for f in facs)
        total_fraud = sum(fraud_per[f] for f in facs)
        rings.append({
            "facilities": facs,
            "n_facilities": len(facs),
            "n_members": len(comp["members"]),
            "shared_members": shared[:25],  # cap: evidence, not a data dump
            "n_shared_members": len(shared),
            "edge_claims": total_claims,
            "claims": total_claims if not fraud_only else sum(all_claims_per[f] for f in facs),
            "fraud_claims": total_fraud,
            "fraud_share": round(total_fraud / max(1, total_claims), 4),
        })
    rings.sort(key=lambda r: (-r["fraud_claims"], -r["n_facilities"]))
    return rings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="lakehouse/gold/claims.jsonl")
    ap.add_argument("--claims", type=int, default=8000)
    ap.add_argument("--min-facilities", type=int, default=2)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--fraud-only", action="store_true",
                    help="build edges from fraud-labelled claims only")
    args = ap.parse_args()
    claims = (D.load_lakehouse_claims(args.gold)
              or D.generate_claims(args.claims, facilities=60))
    labelled = any("fraud" in c for c in claims)
    full = find_rings(claims, min_facilities=args.min_facilities)
    out = {"claims_scanned": len(claims),
           "full_graph": {"n_rings": len(full), "rings": full[: args.top]}}
    if labelled:
        fraud_rings = find_rings(claims, min_facilities=args.min_facilities,
                                 fraud_only=True)
        out["fraud_only_graph"] = {"n_rings": len(fraud_rings),
                                   "rings": fraud_rings[: args.top]}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
