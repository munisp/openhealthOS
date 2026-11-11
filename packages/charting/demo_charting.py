"""charting demo — deterministic seed + checksum-stamped payload.

Same doctrine as vhpilot/vhdashx: a demo you cannot reproduce is a
story you cannot audit. Synthetic clinicians (dr-sim-*), synthetic
patients (Pax-SIM), one command regenerates the payload byte-for-byte.

The demo deliberately exercises the gates, not just the happy path:
one order-set application refuses an allergy conflict and an age-bound
violation — with the reasons stated, because refusals are the feature.
"""

from __future__ import annotations

import hashlib
import json

from charting import NOTE_TEMPLATES, ChartNote, OrderSetRegistry
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

DEMO_VERSION = "charting-demo@1.0.0"


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return dict(payload, sha256=digest)


def seed_charting():
    """A morning in one clinic: two signed notes (one amended), one
    order set applied to two patients — one of whom the gates stop."""
    soap = ChartNote("NOTE-SIM-001", patient_ref="Pax-SIM-0001",
                     author="dr-sim-ayo", template_id="soap-general@1.0.0",
                     day=0)
    soap.set_section("subjective",
                     "Fever and chills for 3 days, headache, no vomiting.")
    soap.set_section("objective",
                     "Temp 38.9C, RR 22, BP 118/76. RDT positive Pf.")
    soap.set_section("assessment", "Uncomplicated malaria (lab-confirmed).")
    soap.set_section("plan", ["artemether-lumefantrine 80/480 bd x 3 days",
                              "paracetamol 1g tds prn",
                              "review in 48h if fever persists"])
    soap.sign(by="dr-sim-ayo", day=0)
    soap.add_addendum(
        by="dr-sim-bola",
        text="Reviewed: added weight-based dosing check — 62kg, dose "
             "appropriate. No change to plan.",
        day=1)

    anc = ChartNote("NOTE-SIM-002", patient_ref="Pax-SIM-0002",
                    author="mw-sim-funke", template_id="anc-visit@1.0.0",
                    day=0)
    anc.set_section("gestational-age-weeks", "28")
    anc.set_section("blood-pressure", {"systolic": 122, "diastolic": 78})
    anc.set_section("fundal-height", "28 cm — corresponds to dates")
    anc.set_section("fetal-heart", "present, 144 bpm")
    anc.set_section("danger-signs-reviewed",
                    ["no vaginal bleeding", "no severe headache",
                     "no facial swelling", "no reduced fetal movement"])
    anc.set_section("plan", ["continue ferrous + folate",
                             "IPTp-SP dose 2 given today",
                             "next visit in 4 weeks"])
    anc.sign(by="mw-sim-funke", day=0)

    order_sets = OrderSetRegistry()
    order_sets.propose(
        "malaria-adult@1.0.0", version="1.0.0",
        label="Uncomplicated malaria — adult",
        items=[{"kind": "medication",
                "label": "artemether-lumefantrine 80/480 bd x 3d",
                "allergy_class": "artemisinins"},
               {"kind": "medication", "label": "paracetamol 1g tds prn",
                "allergy_class": "paracetamol"},
               {"kind": "investigation", "label": "RDT (malaria Pf)",
                "age_min_years": 0},
               {"kind": "medication",
                "label": "artesunate IV (if severe)", "age_min_years": 18,
                "allergy_class": "artemisinins"},
               {"kind": "medication",
                "label": "doxycycline 100mg bd x 7d (alternative)",
                "age_min_years": 12, "allergy_class": "tetracyclines"}],
        by="dr-sim-ayo", day=0)
    order_sets.ratify("malaria-adult@1.0.0", version="1.0.0",
                      by="dr-sim-bola", day=0)

    applied_ok = order_sets.apply(
        "malaria-adult@1.0.0", version="1.0.0",
        patient={"age_years": 34, "allergies": []}, day=0)
    applied_gated = order_sets.apply(
        "malaria-adult@1.0.0", version="1.0.0",
        patient={"age_years": 9, "allergies": ["artemisinins"]}, day=0)

    return {"notes": [soap, anc], "order_sets": order_sets,
            "applications": [applied_ok, applied_gated]}


def charting_payload(book: dict) -> dict:
    """Assemble the demo payload from the seeded book — registries,
    not narrative."""
    notes = [n.as_dict() for n in book["notes"]]
    reg = book["order_sets"]
    sets = [dict(s) for s in reg.sets.values()]
    return _stamp({
        "version": DEMO_VERSION,
        "templates": [
            {"template_id": tid, "label": t["label"],
             "specialty": t["specialty"], "sensitive": t["sensitive"],
             "sections": len(t["sections"])}
            for tid, t in sorted(NOTE_TEMPLATES.items())],
        "notes": notes,
        "order_sets": [{"set_id": s["set_id"], "label": s["label"],
                        "status": s["status"],
                        "items": len(s["items"]),
                        "proposed_by": s["proposed_by"],
                        "ratified_by": s["ratified_by"]}
                       for s in sets],
        "applications": book["applications"],
        "report": reg.report(),
    })


def export_charting(book: dict, out_path: str) -> str:
    payloadio.write_text(out_path, json.dumps(charting_payload(book), indent=2, ensure_ascii=False))
    return out_path
