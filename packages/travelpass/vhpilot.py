"""vhpilot.py — deterministic pilot seed: two weeks of a VHC scheme,
every module exercised, every number reproducible from the seed. This
is demo and test data — synthetic travellers, synthetic providers,
synthetic money — and it says so in every name.

Doctrine: a demo you cannot reproduce is a story you cannot audit.
Same seed, same pilot. Synthetic means synthetic: names are Pax-NNNN,
passports are SIM-NNNN, and nothing here maps to a person.
"""

import random

from vhclaims import ClaimsRegistry
from vhcover import KeyRing, VHCCoverRegistry
from vhfund import FundLedger
from vhproviders import ProviderPortal, ProviderRegistry, TariffBook
from vhregistry import IncidentRegistry
from vhrescue import RescueRegistry, UninsuredFund
from vhsales import PartnerRegistry, purchase_group
from vhsettle import auto_refund_duplicates, reconcile, settlement_file
from vhmarts import actuarial_mart
from vhsupport import ContentLayer, Helpdesk
from crosslink import premium_to_fund

PILOT_VERSION = "vhc-pilot@1.0.0"

SYNDROME_MIX = (("gastrointestinal", 40), ("respiratory", 25),
                ("febrile_rash", 10), ("trauma", 20), ("other", 5))


def _pick(rng, mix):
    total = sum(w for _, w in mix)
    roll = rng.uniform(0, total)
    upto = 0
    for item, weight in mix:
        upto += weight
        if roll <= upto:
            return item
    return mix[-1][0]


def seed_pilot(*, seed=42, days=14, arrivals_per_day=(18, 30),
               salt="pilot-salt"):
    """Run the two-week pilot. Returns the populated registries and a
    narrative with denominators — the walkthrough script reads the
    narrative, never invents numbers."""
    rng = random.Random(seed)

    ring = KeyRing()
    ring.register("pilot-k1", "pilot-signing-key", active_from=0)
    vhc = VHCCoverRegistry(profile_version="cv-vhc@1.0.0", key_ring=ring,
                           issuer="cv-vhc")
    claims = ClaimsRegistry(rules_version="vhc-claims@1.0.0",
                            tariff_set="cv-tariffs")
    rescue = RescueRegistry(rules_version="vhc-rescue@1.0.0")
    fund = FundLedger(rules_version="cv-fund@1.0.0")
    charity = UninsuredFund()
    vreg = IncidentRegistry(rules_version="visitor-incidents@1.0.0",
                            salt=salt)
    providers = ProviderRegistry()
    partners = PartnerRegistry()
    # Deterministic ticket IDs: the pilot is a reproducibility artifact,
    # so its helpdesk injects a seeded counter id_fn (the engine default
    # stays secrets-unique — see vhsupport.Helpdesk).
    _ticket_seq = iter(range(1, 10_000))
    desk = Helpdesk(rules_version="vhc-support@1.0.0",
                    id_fn=lambda: "SIM%04X" % next(_ticket_seq))
    book = TariffBook(book_id="cv")
    content = ContentLayer()

    # providers accredited under four-eyes
    for pid, name in (("HOSP-PRAIA", "Hospital Agostinho Neto"),
                      ("HOSP-MINDELO", "Hospital Baptista de Sousa"),
                      ("CLIN-SAL", "Clinica do Sal")):
        providers.apply(pid, legal_name=name, licence_ref="LIC-" + pid,
                        services=["emergency", "observation"], day=0)
        providers.verify(pid, by="inspector-1",
                         checks=["licence-valid", "facility-visited"],
                         day=0)
        providers.accredit(pid, by="director-1", day=0)
    portal = ProviderPortal(claims=claims, providers=providers)
    partners.register("air-cv", legal_name="Air Cabo Verde",
                      api_key="pilot-air-key", day=0)
    content.publish("pilot@1", lang="pt",
                    entries={"cover.confirmed": "Cobertura confirmada"},
                    by="editor-sim")
    content.publish("pilot@1", lang="en",
                    entries={"cover.confirmed": "Cover confirmed"},
                    by="editor")

    narrative = {"covers": 0, "exempt": 0, "groups": 0,
                 "partner_sales": 0, "verdicts": {"green": 0,
                                                  "amber": 0,
                                                  "red": 0},
                 "claims": 0, "medevacs": 0, "tickets": 0}

    sim_txns = []
    now_h = [0]  # a mutable clock, hour by hour

    def _buy_cover(i, day, via_partner=False):
        age = rng.choices([1, 10, 35, 60], weights=[4, 12, 60, 24])[0]
        stay = rng.choice([7, 14, 30, 40])
        if via_partner:
            q = partners.quote_for(vhc, "air-cv", "pilot-air-key",
                                   days=stay, age_years=age, day=day)
            cover = partners.purchase_for(
                vhc, "air-cv", "pilot-air-key", quote_id=q["quote_id"],
                passport_ref="SIM-%04d" % i, full_name="Pax-%04d" % i,
                entry_day=day, day=day, key_id="pilot-k1")
            narrative["partner_sales"] += 1
        else:
            q = vhc.quote(days=stay, age_years=age, day=day)
            cover = vhc.purchase(quote_id=q["quote_id"],
                                 passport_ref="SIM-%04d" % i,
                                 full_name="Pax-%04d" % i,
                                 entry_day=day, day=day,
                                 key_id="pilot-k1")
        narrative["covers"] += 1
        if q["total_minor"] > 0:
            premium_to_fund(vhc, fund, cover["ref"], day=day)
            sim_txns.append({"ref": cover["ref"],
                             "amount_minor": q["total_minor"],
                             "psp": "stripe", "day": day,
                             "payer_key": "SIM-%04d" % i})
        return cover, age, stay

    pax = 0
    covers = []
    for day in range(1, days + 1):
        n = rng.randint(*arrivals_per_day)
        for _ in range(n):
            pax += 1
            cover, age, stay = _buy_cover(pax, day,
                                          via_partner=(pax % 5 == 0))
            covers.append((cover, day))
        if day % 4 == 0:  # a tour operator's manifest
            members = [{"passport_ref": "GRP-SIM-%d-%d" % (day, k),
                        "full_name": "Grp-%d-%d" % (day, k),
                        "age_years": rng.choice([8, 30, 45]),
                        "days": 14} for k in range(6)]
            purchase_group(vhc, payer_ref="TOURCO-SIM", entry_day=day,
                           travelers=members, day=day, key_id="pilot-k1")
            narrative["groups"] += 1
        if day % 7 == 0:  # exemptions with evidence
            vhc.register_exemption(
                passport_ref="SIM-DIP-%d" % day, full_name="Dip Sim",
                category="diplomatic",
                evidence_ref="MFA-SIM-%d" % day, day=day,
                expires_day=day + 30, key_id="pilot-k1")
            narrative["exempt"] += 1
        # border verifications on a sample of live covers
        for cover, bought_day in covers[-40:]:
            if rng.random() < 0.25:
                v = vhc.verify(cover["qr"], day=day)
                narrative["verdicts"][v["verdict"]] += 1
        # claims: roughly 8% of that day's arrivals
        for cover, bought_day in covers[-n:]:
            if rng.random() < 0.08:
                now_h[0] += 1
                case = claims.intake(
                    channel=rng.choice(("phone", "whatsapp", "kiosk")),
                    cover_ref=cover["ref"],
                    claimant_name=cover["full_name"],
                    summary="simulated ED visit", now_h=now_h[0],
                    day=day)
                claims.issue_gop(case["case_id"],
                                 provider_ref=rng.choice(
                                     ("HOSP-PRAIA", "HOSP-MINDELO",
                                      "CLIN-SAL")),
                                 limit_minor=200_000,
                                 now_h=now_h[0] + 1)  # inside the clock
                provider = case["gop"]["provider_ref"]
                portal.submit_dossier(
                    provider, case["case_id"],
                    icd10_codes=["A09"],
                    acts=[{"code": "CONS-ED", "charged_minor": 7000}],
                    discharge_ref="DIS-SIM-%s" % case["case_id"],
                    day=day)
                claims.adjudicate(case["case_id"], day=day + 1)
                narrative["claims"] += 1
        claims.payment_run(day=day)
        # incidents feed the registry — pseudonymized at the door
        for cover, _d in covers[-n:]:
            if rng.random() < 0.10:
                vreg.register_incident(
                    traveler_key=cover["ref"],
                    syndromic_class=_pick(rng, SYNDROME_MIX),
                    facility=rng.choice(("HOSP-PRAIA", "HOSP-MINDELO",
                                         "CLIN-SAL")),
                    site=rng.choice(("santiago", "svicente", "sal")),
                    outcome=rng.choice(("discharged", "discharged",
                                        "admitted")),
                    cost_minor=rng.choice((2500, 7000, 26000)), day=day,
                    cover_ref=cover["ref"])
        # a GI cluster at CLIN-SAL in week 2 — BR-10's moment
        if day == 9:
            for k in range(5):
                vreg.register_incident(
                    traveler_key="SIM-CLUSTER-%d" % k,
                    syndromic_class="gastrointestinal",
                    facility="CLIN-SAL", site="sal",
                    outcome="discharged", cost_minor=2500, day=day)
        alerts = vreg.evaluate_alerts(day=day)
        for alert_id in alerts["opened"]:
            vreg.acknowledge(alert_id, by="duty-officer-sim",
                             note="cultures ordered, water inspection",
                             day=day)
        # helpdesk traffic
        if rng.random() < 0.6:
            now_h[0] += 1
            t = desk.open_ticket(
                category=rng.choice(desk.rules["categories"]),
                channel=rng.choice(desk.rules["channels"]),
                priority="standard", subject="sim enquiry",
                cover_ref="SIM", now_h=now_h[0], day=day)
            desk.respond(t["ticket_id"], by="agent-sim", note="checking",
                         now_h=now_h[0] + 2)
            desk.resolve(t["ticket_id"], by="agent-sim",
                         resolution="resolved", now_h=now_h[0] + 10)
            narrative["tickets"] += 1
        # one medevac in week 2
        if day == 10 and claims.cases:
            some_case = next(iter(claims.cases.values()))
            med = rescue.request_medevac(
                some_case, from_facility="CLIN-SAL",
                to_destination="HOSP-PRAIA",
                clinical_justification="simulated deterioration",
                requested_by="dr-sim", now_h=now_h[0])
            rescue.approve(med["mission_id"], by="medical-director",
                           note="indicated", now_h=now_h[0] + 1)
            rescue.dispatch(med["mission_id"], asset="fixed-wing",
                            now_h=now_h[0] + 2)
            rescue.arrive(med["mission_id"], now_h=now_h[0] + 5)
            rescue.close(med["mission_id"], outcome="handed over",
                         now_h=now_h[0] + 6)
            narrative["medevacs"] += 1

    # weekly settlement + reconciliation
    recon_reports = []
    for day in (7, 14):
        stl = settlement_file(sim_txns, day=day, psp="stripe")
        psp_rows = [{"ref": t["ref"], "amount_minor": t["amount_minor"]}
                    for t in sim_txns if t["day"] == day]
        recon_reports.append(reconcile(sim_txns, day=day, psp="stripe",
                                       psp_rows=psp_rows))
    refunds = auto_refund_duplicates(sim_txns, day=days)

    pack = vreg.export_pack(export_format="ihr", week=1)
    mart = actuarial_mart(vhc=vhc, claims=claims, incidents=vreg,
                          through_day=days)

    narrative.update({
        "version": PILOT_VERSION, "seed": seed, "days": days,
        "arrivals": pax,
        "premium_collected_minor": fund.report()["allocated_minor"],
        "claims_paid_minor": claims.report()["paid_minor"],
        "loss_ratio": mart["loss_ratio"],
        "br10_alerts": len(vreg.alerts),
        "br10_all_acknowledged": all(a["acknowledged"]
                                     for a in vreg.alerts.values()),
        "export_pack_sha256": pack["pack_sha256"],
        "settlement_breaches": sum(1 for r in recon_reports
                                   if r["breach"]),
        "auto_refunds": len(refunds),
        "helpdesk_sla": desk.report()["first_response_sla"],
        "providers_accredited":
            providers.report()["by_state"]["accredited"],
        "registry_cost_minor": vreg.report()["total_cost_minor"],
    })
    return {"registries": {
                "vhc": vhc, "claims": claims, "rescue": rescue,
                "fund": fund, "charity": charity, "vreg": vreg,
                "providers": providers, "partners": partners,
                "desk": desk, "book": book, "content": content},
            "narrative": narrative}
