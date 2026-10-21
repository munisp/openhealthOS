"""spike.py — stdlib-only reference engine for the AGE spike.

Executes the SAME logic as the three killer openCypher queries in
schema.sql over the synthetic dataset, and pins golden outputs. The live
AGE run (run_age.sh --verify) must reproduce these results exactly before
adoption is declared proven. This engine is a test fixture, not a
production graph store — the production candidate is AGE itself.

Graph model: adjacency dicts, BFS for 2-hop, fan-in/fan-out tallies for
fraud signatures, edge aggregation for referral flow. Honest thresholds,
documented per query.
"""
from collections import defaultdict

# fraud signature thresholds (documented, tuneable):
RING_MIN_CLAIMS = 15      # facility filed at least this many claims
RING_MAX_SIGNERS = 1      # ...all signed by at most this many prescribers
RING_MAX_ACCOUNTS = 2     # ...paying into at most this few accounts


class RefGraph:
    def __init__(self, nodes, edges):
        self.labels = {n["id"]: n["label"] for n in nodes}
        self.kinds = {n["id"]: n.get("kind") for n in nodes}
        self.out = defaultdict(list)   # src -> [(type, dst, props)]
        self.inc = defaultdict(list)   # dst -> [(type, src, props)]
        for ed in edges:
            self.out[ed["src"]].append((ed["type"], ed["dst"], ed))
            self.inc[ed["dst"]].append((ed["type"], ed["src"], ed))

    def _ids(self, label):
        return {i for i, l in self.labels.items() if l == label}

    # --- killer query 1: fraud-ring signature ------------------------------
    # facilities with MANY claims, ONE signer, FEW payout accounts.
    def fraud_rings(self):
        rings = []
        for fac in self._ids("Facility"):
            claims = [src for t, src, _ in self.inc[fac] if t == "FILED_BY"]
            if len(claims) < RING_MIN_CLAIMS:
                continue
            signers, accounts = set(), set()
            for c in claims:
                signers |= {d for t, d, _ in self.out[c] if t == "SIGNED_BY"}
                accounts |= {d for t, d, _ in self.out[c] if t == "PAID_TO"}
            if len(signers) <= RING_MAX_SIGNERS and len(accounts) <= RING_MAX_ACCOUNTS:
                rings.append({"facility": fac, "claims": len(claims),
                              "prescribers": sorted(signers),
                              "accounts": sorted(accounts)})
        return sorted(rings, key=lambda r: r["facility"])

    # --- killer query 2: contact 2-hop --------------------------------------
    # patients who attended an encounter that the index case attended.
    def contacts_2hop(self, index_patient):
        encs = [d for t, d, _ in self.out[index_patient] if t == "ATTENDED"]
        contacts = set()
        for enc in encs:
            contacts |= {s for t, s, _ in self.inc[enc] if t == "ATTENDED"}
        contacts.discard(index_patient)
        return sorted(contacts)

    # --- killer query 3: referral flow ---------------------------------------
    # aggregate REFERRED_TO edges; find orphans (no referrals out, PHC only)
    # and the top destination into tertiary.
    def referral_flow(self):
        flow = defaultdict(int)
        for fac in self._ids("Facility"):
            for t, d, props in self.out[fac]:
                if t == "REFERRED_TO":
                    flow[(fac, d)] += int(props.get("count", 0))
        # orphan = facility that refers no one AND is not the top of the
        # chain (tertiary has no one to refer to by design).
        orphans = sorted(f for f in self._ids("Facility")
                         if self.kinds.get(f) != "tertiary"
                         and not any(t == "REFERRED_TO" for t, _, _ in self.out[f]))
        inbound = defaultdict(int)
        for (_, dst), c in flow.items():
            inbound[dst] += c
        top = max(inbound.items(), key=lambda kv: kv[1])[0] if inbound else None
        return {"flows": sorted([{"from": s, "to": d, "count": c}
                                 for (s, d), c in flow.items()],
                                key=lambda f: (f["from"], f["to"])),
                "orphan_facilities": orphans, "top_destination": top}


def run_spike(data):
    g = RefGraph(data["nodes"], data["edges"])
    rings = g.fraud_rings()
    return {
        "fraud_ring_facilities": [r["facility"] for r in rings],
        "fraud_ring_prescribers": sorted({p for r in rings for p in r["prescribers"]}),
        "fraud_ring_accounts": sorted({a for r in rings for a in r["accounts"]}),
        "contact_2hop_from_PAT-0001": g.contacts_2hop("PAT-0001"),
        "orphan_facilities": g.referral_flow()["orphan_facilities"],
        "top_referral_into_tertiary": g.referral_flow()["top_destination"],
    }


if __name__ == "__main__":
    import json
    from synthdata import generate
    print(json.dumps(run_spike(generate()), indent=2))
