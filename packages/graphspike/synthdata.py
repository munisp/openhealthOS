"""synthdata.py — deterministic synthetic graph dataset for the AGE spike.

Mirrors synthseed doctrine: seeded, honest, no real identifiers. Plants
exactly one fraud ring, one contact cluster, and a plausible referral
network so the three killer queries have known-correct answers (goldens).
"""
import csv
import io
import random

SEED = 20261021


def generate(seed: int = SEED) -> dict:
    rng = random.Random(seed)
    nodes, edges = [], []

    def n(label, nid, **props):
        nodes.append({"label": label, "id": nid, **props})

    def e(etype, src, dst, **props):
        edges.append({"type": etype, "src": src, "dst": dst, **props})

    # --- real facilities: 8 PHCs, 3 general hospitals, 1 tertiary ---------
    phcs = [f"OHF-FC-AMC-{i:04d}" for i in range(1, 9)]
    gens = ["OHF-FC-AMC-0101", "OHF-LA-IKE-0102", "OHF-KD-KAF-0103"]
    tert = ["OHF-FC-AMC-0201"]
    for f in phcs:
        n("Facility", f, kind="phc", lga="amc")
    for f in gens:
        n("Facility", f, kind="general", lga=f.split("-")[2].lower())
    for f in tert:
        n("Facility", f, kind="tertiary", lga="amc")

    # --- referral network (killer query 3): PHC -> general -> tertiary ----
    for i, phc in enumerate(phcs):
        if i == 7:
            continue  # phcs[7]: deliberately orphaned, no referral edges
        g = gens[i % len(gens)]
        e("REFERRED_TO", phc, g, count=rng.randint(4, 12))
    for g in gens:
        e("REFERRED_TO", g, tert[0], count=rng.randint(50, 60))

    # --- workforce ----------------------------------------------------------
    prescribers = [f"PRV-{i:03d}" for i in range(1, 21)]
    for p in prescribers:
        n("Prescriber", p, council="MDCN")
        e("WORKS_AT", p, rng.choice(phcs + gens))

    # --- patients -----------------------------------------------------------
    patients = [f"PAT-{i:04d}" for i in range(1, 121)]
    for p in patients:
        n("Patient", p)

    # --- encounters for contact tracing (killer query 2) -------------------
    # routine encounters: low overlap background noise
    enc_id = 0
    for p in patients[:80]:
        enc_id += 1
        enc = f"ENC-{enc_id:04d}"
        n("Encounter", enc, kind="outpatient")
        e("ATTENDED", p, enc)
        e("HELD_AT", enc, rng.choice(phcs))
    # PLANTED CONTACT CLUSTER: one market-day outreach session, 12 attendees,
    # including index case PAT-0001. 2-hop query from PAT-0001 must find
    # exactly the other 11.
    cluster_enc = "ENC-9001"
    n("Encounter", cluster_enc, kind="outreach", note="market-day")
    cluster_members = ["PAT-0001"] + rng.sample(patients[1:], 11)
    for p in cluster_members:
        e("ATTENDED", p, cluster_enc)
    e("HELD_AT", cluster_enc, phcs[2])

    # --- claims + payouts (killer query 1) ---------------------------------
    accounts = [f"ACC-{i:02d}" for i in range(1, 15)]
    for a in accounts:
        n("Account", a)
    claim_id = 0
    # honest background claims: many facilities, many prescribers, many accounts
    for _ in range(200):
        claim_id += 1
        c = f"CLM-{claim_id:05d}"
        n("Claim", c, amount=rng.randint(1500, 40000))
        e("FILED_BY", c, rng.choice(phcs + gens))
        e("SIGNED_BY", c, rng.choice(prescribers))
        e("PAID_TO", c, rng.choice(accounts))
    # PLANTED FRAUD RING: ghost facility OHF-XX-GHOST-9999 + prescriber
    # PRV-666 + 2 shared payout accounts, claims that reference patients who
    # were never seen there. Signature: one facility files MANY claims, ALL
    # signed by one prescriber, ALL paying into only 2 accounts -> a tight
    # star with account fan-in. Ring = ghost facility, PRV-666, ACC-13, ACC-14.
    ghost = "OHF-XX-GHOST-9999"
    n("Facility", ghost, kind="phc", lga="amc")
    n("Prescriber", "PRV-666", council="MDCN")
    e("WORKS_AT", "PRV-666", ghost)
    ring_accounts = ["ACC-13", "ACC-14"]
    for i in range(30):
        claim_id += 1
        c = f"CLM-{claim_id:05d}"
        n("Claim", c, amount=rng.randint(20000, 60000))
        e("FILED_BY", c, ghost)
        e("SIGNED_BY", c, "PRV-666")
        e("PAID_TO", c, ring_accounts[i % 2])

    return {
        "nodes": nodes,
        "edges": edges,
        "golden": {
            "fraud_ring_facilities": [ghost],
            "fraud_ring_prescribers": ["PRV-666"],
            "fraud_ring_accounts": sorted(ring_accounts),
            "contact_2hop_from_PAT-0001": sorted(p for p in cluster_members if p != "PAT-0001"),
            # honest answer: the ghost facility also refers no one — a
            # claims-without-referrals facility is itself a fraud signal.
            "orphan_facilities": sorted([ghost, phcs[7]]),
            "top_referral_into_tertiary": tert[0],
        },
    }


def to_csvs(data: dict) -> dict:
    """Render nodes/edges as CSV text for AGE bulk load."""
    out = {}
    for key in ("nodes", "edges"):
        buf = io.StringIO()
        rows = data[key]
        cols = sorted({k for r in rows for k in r})
        w = csv.DictWriter(buf, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
        out[key] = buf.getvalue()
    return out


if __name__ == "__main__":
    import json
    d = generate()
    print(f"nodes={len(d['nodes'])} edges={len(d['edges'])}")
    print(json.dumps(d["golden"], indent=2))
