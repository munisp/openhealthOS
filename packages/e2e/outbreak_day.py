"""outbreak_day.py — the Ebola importation scenario, composed end to end.

One executable narrative proving the ten outbreak modules work as ONE
system, not ten libraries. The scenario mirrors the October 2026 FCT
importation shape: a traveller arrives from an affected country, screens
positive at the border, becomes a case, exposes contacts, one converts,
labs run, signals resolve, the ring forms — and the after-action review
writes itself from the records.

Every arrow below is a real function call; the asserts pin the doctrine:
the trace starts at the border, conversion spawns a ring, the lab clock
runs, precision is computed, and the AAR contradicts nothing.
"""
import os
import sys

ROOT = os.path.dirname(__file__)
for pkg in ("contacttrace", "casedef", "ebs", "labtrack", "poe_screen",
            "capacity", "ringops", "riskcomm", "signalledger", "aar"):
    sys.path.insert(0, os.path.join(ROOT, "..", pkg))

from contacttrace import ContactGraph  # noqa: E402
from casedef import CaseDefRegistry  # noqa: E402
from ebs import SignalInbox  # noqa: E402
from labtrack import LabTracker  # noqa: E402
from poe_screen import AlertList, BorderPost  # noqa: E402
from capacity import CapacityBoard  # noqa: E402
from ringops import Ring  # noqa: E402
from riskcomm import Campaign  # noqa: E402
from ledger import SignalLedger  # noqa: E402
from aar import render_aar  # noqa: E402

EBOLA_DEF = {
    "version": "ebola-v1",
    "suspect_criteria": {"fever": True, "travel_or_contact_21d": True},
    "confirmed_criteria": {"lab_pcr_positive": True},
}


def run_outbreak() -> dict:
    log = []

    # 1. Border: alert list published; the traveller screens positive.
    alerts = AlertList()
    alerts.publish("al-v1", ["Uganda", "DR Congo"], "2026-10-20")
    poe = BorderPost("NAIA-ABUJA", alerts)
    screen = poe.screen("TRAVELLER-1", "al-v1", ["Uganda", "Ethiopia"],
                        38.9, ["headache", "fatigue"], "poe-officer-a",
                        "2026-10-20")
    assert screen["disposition"] == "isolate-and-notify"
    assert screen["trace_seed"]["exposure_day"] == "2026-10-20"
    log.append("border: isolate-and-notify, trace seeded at the border")

    # 2. Case definition pinned; screening confirms suspect status.
    casedefs = CaseDefRegistry()
    casedefs.activate("ebola", EBOLA_DEF, "2026-10-20", author="ncdc")
    suspect = casedefs.screen(
        "ebola", "ebola-v1",
        {"fever": True, "travel_or_contact_21d": True},
        "NAIA-ABUJA", "2026-10-20")
    assert suspect["disposition"] == "suspect"
    log.append("casedef: suspect under ebola-v1, pinned and audited")

    # 3. Capacity: a verified isolation centre takes the patient.
    board = CapacityBoard()
    board.attest("UATH", 20, 6, 4.0, 12, "2026-10-20", "admin-uath")
    board.attest("OLD-CTR", 10, 8, 5.0, 6, "2026-10-01", "admin-old")
    placement = board.where("2026-10-20", need_beds=1, min_ppe_days=1.0)
    assert placement["ok"] and placement["options"][0]["centre"] == "UATH"
    assert placement["stale_noted"] == 1
    log.append("capacity: UATH verified; OLD-CTR stale and not counted")

    # 4. Lab: sample registered, lifecycle runs, result positive.
    labs = LabTracker()
    labs.register("S-1", "CASE-1", "NRL", "2026-10-20T14:00:00Z")
    labs.advance("S-1", "in-transit", "2026-10-20T16:00:00Z")
    labs.advance("S-1", "received", "2026-10-20T20:00:00Z")
    labs.result("S-1", "positive", "2026-10-21T08:00:00Z")
    assert labs.samples["S-1"]["result"] == "positive"
    log.append("lab: PCR positive, 18h turnaround")

    # 5. Contact graph: the case spawns contacts; one converts.
    graph = ContactGraph()
    graph.add_case("CASE-1", "2026-10-21")
    for cid, tracer in (("C-01", "tracer-a"), ("C-02", "tracer-a"),
                        ("C-03", "tracer-b")):
        graph.add_contact("CASE-1", cid, "2026-10-07", tracer=tracer)
    graph.checkin("C-01", "2026-10-22")
    conv = graph.checkin("C-02", "2026-10-22", symptomatic=True)
    assert conv["action"] == "create-case-and-start-ring"
    graph.add_case("CASE-2", "2026-10-22", source_contact_id="C-02")
    graph.add_contact("CASE-2", "C-10", "2026-10-22", tracer="tracer-a")
    assert graph.contacts["C-10"]["generation"] == 2
    sweep = graph.sweep("2026-10-23")
    assert "C-03" in sweep["overdue"]  # never checked in
    log.append("trace: C-02 converted (case 2, generation 2); C-03 overdue")

    # 6. EBS: a community rumour dedupes, verifies, becomes a draft.
    inbox = SignalInbox()
    r1 = inbox.intake("two sudden deaths after burial with bleeding",
                      "community-call", "Kawo", "2026-10-22", "leader-1")
    inbox.triage(r1["id"], verifier="epi-fct", by="desk", day="2026-10-22")
    inbox.begin_verification(r1["id"], "2026-10-22")
    resolved = inbox.resolve(r1["id"], "dismissed",
                             "team visited: deaths pre-outbreak, unrelated "
                             "cause confirmed", "2026-10-23")
    assert resolved["signal_draft"] is None
    log.append("ebs: rumour verified-as-dismissed; no false alert shipped")

    # 7. Signal ledger: Scout's true uptick labelled, precision computed.
    ledger = SignalLedger()
    ledger.register("SIG-1", "scout", "2026-10-20", 2.0)
    ledger.register("SIG-2", "ebs", "2026-10-22", 2.0)
    ledger.label("SIG-1", "verified-case", "2026-10-21", "CASE-1 PCR+")
    ledger.label("SIG-2", "false-alarm", "2026-10-23",
                 "dismissed after field verification")
    prec = ledger.precision_report("2026-10-23")
    assert prec["by_source"]["scout"]["precision"] == 1.0
    assert prec["unresolved_count"] == 0
    log.append("ledger: scout precision 1.0, zero unresolved")

    # 8. Ring: generated from the graph; consent before dose.
    ring = Ring.from_graph("RING-1", "CASE-1", graph)
    assert set(ring.people) == {"C-01", "C-02", "C-03"}
    ring.consent("C-01", "2026-10-23")
    ring.dose("C-01", "2026-10-23", "vacc-1")
    ring.decline("C-03", "awaiting family decision", "2026-10-23")
    comp = ring.completeness()
    assert comp["completeness"] == round(2 / 3, 3)
    log.append("ring: 2/3 accounted, decline in the denominator")

    # 9. Risk comms: contacts messaged; ack honesty.
    camp = Campaign("ebola-fct-oct26", "msg-v1",
                    {"en": "You are a contact of a confirmed case. Report "
                           "fever immediately. Daily check-ins required.",
                     "ha": "Kai mai mu'amala ne da tabbacin cuta. A kai "
                           "rahoto zazzabi nan take."})
    for cid in ("C-01", "C-02", "C-03"):
        camp.target(cid, "en")
    camp.mark_delivered("C-01")
    camp.acknowledge("C-01")
    camp.mark_delivered("C-02")
    rep = camp.report()
    assert rep["acknowledged"] == 1
    assert set(rep["unacknowledged_followup_list"]) == {"C-02", "C-03"}
    log.append("riskcomm: 1 acked, 2 on the follow-up list")

    # 10. AAR: the review writes itself from the records.
    md = render_aar("Ebola Bundibugyo — FCT importation (simulated)",
                    ("2026-10-20", "2026-10-23"),
                    graph=graph, labs=labs, ledger=ledger,
                    casedefs=casedefs, casedef_name="ebola", rings=[ring],
                    notes=["C-03 unacknowledged and overdue — priority "
                           "follow-up"])
    assert "Cases: 2" in md and "Contacts line-listed: 4" in md
    assert "precision 100.0%" in md
    assert "RING-1" in md
    assert "— ABSENT" not in md
    log.append("aar: review generated, contradicts nothing")

    return {"log": log, "aar": md}
