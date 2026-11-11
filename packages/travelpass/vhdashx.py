"""vhdashx — growth & risk payload exporter (vhc-growth@1.0.0).

The first dashboard wave showed the scheme's spine: fund, BR-10,
helpdesk. This wave shows the growth book the innovation modules
create — diaspora sponsorships, pay-small-small credit, campus
cohorts, ECOWAS roaming, collusion-ring scans, and per-corridor
treasury exposure — all computed by running the engines on a
deterministic synthetic seed, never narrated.

Doctrine carried forward: the exporter reads registries, not stories;
unpriced exposure is stated as unpriced, never zeroed; template
country profiles refuse to price until ratified, and the payload
says how many are still templates. The checksum is the contract.
"""

from __future__ import annotations

import os
import sys
import hashlib
import json
import random

from vhcover import KeyRing, VHCCoverRegistry, VHC_PROFILES
from vhcohorts import CohortRegistry
from vhcredit import CreditBook
from vhfraudgraph import FraudGraph
from vhroam import compose_roam_profile, price_roam, verdict_rule
from vhsponsor import SponsorBook
from vhtreasury import corridor_report, report as treasury_report
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

GROWTH_VERSION = "vhc-growth@1.0.0"


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def seed_growth(*, seed=7):
    """A deterministic growth book. Synthetic throughout: sponsors are
    SPN-SIM, plans are PLN-SIM, students are MBR-SIM. Same seed, same
    book — a demo you cannot reproduce is a story you cannot audit."""
    rng = random.Random(seed)

    # --- diaspora sponsorships, priced through the cover engine ------
    ring = KeyRing()
    ring.register("growth-k1", "growth-signing-key", active_from=0)
    vhc = VHCCoverRegistry(profile_version="ng-vhc@1.0.0", key_ring=ring,
                           issuer="ng-vhc")
    sponsors = SponsorBook()
    sponsor_ccys = ("USD", "GHS", "KES", "ZAR")
    for i in range(12):
        sponsors.sponsor_cover(
            vhc,
            sponsor_ref=f"SPN-SIM-{i:03d}",
            sponsor_currency=sponsor_ccys[i % len(sponsor_ccys)],
            traveller_passport=f"SIM-{9000 + i}",
            traveller_name=f"Traveller-Sim-{i:03d}",
            days=rng.choice((7, 14, 21, 30)),
            age_years=rng.choice((24, 31, 38, 47, 55)),
            day=i // 3, key_id="growth-k1")

    # --- pay-small-small credit book --------------------------------
    credit = CreditBook()
    for i in range(8):
        per_day = rng.choice((15000, 20000, 25000))       # kobo
        window = rng.choice((14, 21, 30))
        plan = credit.open_plan(
            f"PLN-SIM-{i:03d}", cover_ref=f"VHC-SIM-{i:03d}",
            total_minor=per_day * window, per_day_minor=per_day,
            installments=4, window_days=window, currency="NGN", day=0)
        # most plans pay on time; two fall behind and pause at the
        # vested boundary — stated, never a silent lapse
        sched = plan.schedule()
        paid = 4 if i < 5 else (2 if i < 7 else 1)
        for k in range(paid):
            plan.pay(sched[k], day=3 * (k + 1), ref=f"PAY-SIM-{i:03d}-{k}")
        if i >= 5:
            plan.check_default(day=3 * paid + 4,
                               due_minor=sum(sched[:paid + 1]))

    # --- campus cohort (Unilag-style, synthetic) --------------------
    cohorts = CohortRegistry()
    cohorts.create("unilag-sim-2026", institution="University-Sim-Lagos",
                   country="ng", term_days=90,
                   price_per_member_minor=250000, currency="NGN",
                   ratified_by="bursar-sim", approved_by="registrar-sim",
                   day=0)
    for i in range(40):
        cohorts.add_member("unilag-sim-2026",
                           member_ref=f"MBR-SIM-{i:04d}",
                           full_name=f"Student-Sim-{i:04d}", day=0)
    cohorts.close_census("unilag-sim-2026", day=1, by="registrar-sim")

    # --- ECOWAS roam sample over the two ratified members -----------
    roam = compose_roam_profile("ecowas-roam@1.0.0", countries=("ng", "cv"),
                                label="ECOWAS roam — ratified members only")
    roam_price = price_roam(roam, segments={"ng": 6, "cv": 5},
                            age_years=33)
    roam_verdict = verdict_rule(roam, ["green", "amber", "green"])

    # --- collusion-ring scan -----------------------------------------
    graph = FraudGraph()
    providers = [f"clinic-sim-{j}" for j in range(6)]
    for i in range(15):
        graph.add_claim(claim_ref=f"CLM-SIM-N{i:03d}",
                        claimant=f"pax-sim-{i:03d}",
                        provider=providers[i % len(providers)],
                        payout_account=f"acct-sim-{i % 9}")
    # the ring: three claimants, one provider, one payout account
    for k, claimant in enumerate(("pax-sim-r1", "pax-sim-r2",
                                  "pax-sim-r3", "pax-sim-r1")):
        graph.add_claim(claim_ref=f"CLM-SIM-R{k}", claimant=claimant,
                        provider="clinic-sim-7",
                        payout_account="acct-sim-99")

    # --- treasury corridors: sponsor inflows, cohort invoice, roam --
    lines = []
    for rec in sponsors.sponsorships.values():
        q = rec["sponsor_quote"]
        lines.append({"payer": rec["sponsor_ref"],
                      "amount_minor": q["amount_minor"],
                      "currency": q["to"], "ref": rec["ref"],
                      "corridor": "diaspora->ng"})
    inv = cohorts.invoice("unilag-sim-2026")
    lines.append({"payer": "unilag-sim-2026",
                  "amount_minor": inv["total_minor"],
                  "currency": inv["currency"], "ref": inv["invoice_sha256"][:10],
                  "corridor": "campus-ng"})
    for ln in roam_price["lines"]:
        lines.append({"payer": "roam-sample",
                      "amount_minor": ln["minor"],
                      "currency": ln["currency"], "ref": ln["profile"],
                      "corridor": "roam-ecowas"})
    # an honestly unpriced corridor: no stated GBP rate — the payload
    # must say unpriced, never zero
    lines.append({"payer": "charter-sim-gb", "amount_minor": 420000,
                  "currency": "GBP", "ref": "CHT-SIM-01",
                  "corridor": "charter-gb"})

    return {"vhc": vhc, "sponsors": sponsors, "credit": credit,
            "cohorts": cohorts, "roam": roam, "roam_price": roam_price,
            "roam_verdict": roam_verdict, "graph": graph,
            "settlement_lines": lines, "seed": seed}


def growth_payload(book: dict) -> dict:
    """Assemble the growth/risk payload from a seeded book. Every
    number comes out of an engine's own report — nothing is rounded
    into a story."""
    sponsors = book["sponsors"]
    sp_recs = list(sponsors.sponsorships.values())
    sp_by_ccy = {}
    for rec in sp_recs:
        q = rec["sponsor_quote"]
        # the sponsor pays in their own currency: that is the quote's
        # "to" side (profile currency -> sponsor currency)
        b = sp_by_ccy.setdefault(q["to"], {"currency": q["to"],
                                           "minor": 0, "count": 0})
        b["minor"] += q["amount_minor"]
        b["count"] += 1

    credit = book["credit"]
    credit_states = {}
    booked = paid_minor = 0
    for p in credit.plans.values():
        st = p.status(p.created_day)["state"]
        credit_states[st] = credit_states.get(st, 0) + 1
        booked += p.total_minor
        paid_minor += p.paid_minor

    coh = book["cohorts"]
    inv = coh.invoice("unilag-sim-2026")

    graph = book["graph"]
    rings = graph.detect_rings()
    ring_rows = [{"provider": r["provider"], "account": r["account"],
                  "claimants": len(r["claimants"]), "claims": r["claims"],
                  "evidence_sha256": graph.evidence_pack(r)["sha256"]}
                 for r in rings]

    corridors = corridor_report(book["settlement_lines"])
    treas = treasury_report(book["settlement_lines"])

    ratified = sorted(pid for pid, p in VHC_PROFILES.items()
                      if p.get("status") == "ratified")
    templates = sorted(pid for pid, p in VHC_PROFILES.items()
                       if p.get("status") == "template")

    return _stamp({
        "version": GROWTH_VERSION,
        "seed": book["seed"],
        "sponsors": {
            "sponsorships": len(sp_recs),
            "by_currency": sorted(sp_by_ccy.values(),
                                  key=lambda b: b["currency"]),
            "destination_currency": "NGN",
        },
        "credit": {
            "plans": len(credit.plans),
            "by_state": credit_states,
            "booked_minor": booked,
            "paid_minor": paid_minor,
            "currency": "NGN",
        },
        "cohorts": {
            "cohorts": coh.report()["cohorts"],
            "members": inv["members"],
            "invoice_total_minor": inv["total_minor"],
            "currency": inv["currency"],
            "invoice_sha256": inv["invoice_sha256"],
        },
        "roam": {
            "profile": book["roam"]["profile_id"],
            "members": list(book["roam"]["countries"]),
            "sample_segments": book["roam_price"]["lines"],
            "totals_by_currency": book["roam_price"]["totals_by_currency"],
            "sample_verdict": book["roam_verdict"],
        },
        "fraud": {
            "claims_scanned": graph.report()["claims"],
            "rings": len(rings),
            "thresholds": graph.report()["thresholds"],
            "ring_rows": ring_rows,
        },
        "treasury": {
            "corridors": corridors,
            "corridor_count": treas["corridors"],
            "currencies": treas["currencies"],
            "total_priced_eur_minor": treas["total_eur_minor"],
            "unpriced": treas["unpriced"],
        },
        "profiles": {"ratified": ratified, "templates": templates},
    })


def export_growth(book: dict, out_dir: str) -> str:
    """Write vhc-growth.json into out_dir (the PWA directory, so the
    page fetches it same-origin). Returns the path written."""
    import os
    path = os.path.join(out_dir, "vhc-growth.json")
    payloadio.write_text(path, json.dumps(growth_payload(book), indent=2, ensure_ascii=False))
    return path
