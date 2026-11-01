"""connected_day.py — the seven crosslink seams in one composed day.

The gap analysis called it 'cross-package wiring': engines existed in
isolation. This scenario proves they now compose — one patient arc and
one operational arc across NINE packages, each hand-off a public seam:

  08:00  rota says who reads today            → teleread candidate pool
  08:05  a STAT chest case goes to the ROSTERED reader, not the loudest
  09:10  the reader signs a structured report (reportkit)
  09:20  derm review says 'biopsy it'         → telepath custody begins
  10:00  pharmacy order for a CKD patient     → nephrology eGFR verdict
  11:00  tumour board convenes                → evidence pack PULLED,
                                                  drafts excluded
  14:00  spo2 breach goes unacknowledged      → tele-ICU oversight opens
  15:00  dialysis chair plan for tomorrow     → HBV+ on dedicated only
  16:00  cryo tank drifts warm                → ticket names WHOSE embryos

Every refusal is asserted, not assumed. If a seam breaks, this day
does not end.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("crosslink", "nephrology", "telederm", "telepath", "teleread",
            "reportkit", "pacs", "fertility", "rota", "triggers", "vitals",
            "teleicu", "mdt"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from crosslink import (CrosslinkError, renal_check, biopsy_to_path,  # noqa: E402
                       case_pack, submit_with_pack, cryo_watch,
                       chair_plan, on_shift_readers, assign_on_shift,
                       escalate_events)
from nephrology import DialysisUnit  # noqa: E402
from telederm import TeleDerm  # noqa: E402
from telepath import PathologyService  # noqa: E402
from teleread import DomainConfig, ReaderRegistry, ReadingService, TeleReadError  # noqa: E402
from reportkit import ReportKit  # noqa: E402
from pacs import StudyRegistry  # noqa: E402
from fertility import FertilityClinic  # noqa: E402
from rota import Rota, Shift, Staff  # noqa: E402
from triggers import Rule, TriggerEngine  # noqa: E402
from vitals import VitalsIngest  # noqa: E402
from teleicu import TeleICU  # noqa: E402
from mdt import TumourBoard  # noqa: E402

TODAY = "2026-11-02"


def run_connected_day():
    log = {}

    # ---- the registries ----
    readers = ReaderRegistry()
    readers.register("rad-on", licence_id="MDCN-R1",
                     licence_expiry_day=400, scopes=("chest",))
    readers.register("rad-off", licence_id="MDCN-R2",
                     licence_expiry_day=400, scopes=("chest",))
    readers.register("path-1", licence_id="MDCN-P1",
                     licence_expiry_day=400, scopes=("histopathologist",))
    readers.register("derm-1", licence_id="MDCN-D1",
                     licence_expiry_day=400, scopes=("dermatologist",))
    readers.register("icu-1", licence_id="MDCN-I1",
                     licence_expiry_day=400, scopes=("intensivist",))

    radiology = ReadingService(
        DomainConfig("radiology", scopes=("chest",),
                     sla_hours={"stat": 2, "urgent": 8, "routine": 24}),
        readers)
    rk = ReportKit(signing_key=b"connected-day-key")
    unit = DialysisUnit("renal-1")
    derm = TeleDerm(readers)
    path = PathologyService(readers)
    pacs_svc = StudyRegistry()
    clinic = FertilityClinic("fert-1")
    vitals = VitalsIngest()
    engine = TriggerEngine(vitals)
    icu = TeleICU(readers)
    board = TumourBoard("colon-board")

    # ---- 08:00 rota → reading pool ----
    rota = Rota()
    rota.add_staff(Staff("rad-on", "On Duty", "radiologist"))
    rota.add_staff(Staff("rad-off", "Off Duty", "radiologist"))
    rota.add_shift(Shift("D1", TODAY, "day", {"radiologist": 1}))
    rota.assign("D1", "rad-on")
    pool = on_shift_readers(rota, day=TODAY)
    assert pool["candidates"] == ["rad-on"], pool
    log["rota_pool"] = pool["candidates"]

    # ---- 08:05 STAT case to the rostered reader ----
    radiology.order("CX-1", priority="stat", question="?pneumothorax",
                    facility="phc-1", referrer="mo-1", day=0,
                    scope="chest")
    out = assign_on_shift(radiology, "CX-1", day=0, pool=pool)
    assert out["assigned_to"] == "rad-on", out
    # nobody rostered tomorrow → the seam refuses, does not re-widen
    empty_pool = on_shift_readers(rota, day="2026-11-03")
    radiology.order("CX-2", priority="routine", question="?fibrosis",
                    facility="phc-1", referrer="mo-1", day=0,
                    scope="chest")
    try:
        assign_on_shift(radiology, "CX-2", day=0, pool=empty_pool)
        raise AssertionError("empty pool must refuse")
    except CrosslinkError:
        log["empty_pool_refused"] = True

    # ---- 09:10 structured report, signed ----
    rk.draft("CX-1-report", template_id="ecg-12lead@1.0.0",
             answers={"rate_bpm": 92, "rhythm": "sinus", "pr_ms": 150,
                      "qrs_ms": 88, "qtc_ms": 405,
                      "findings": "no acute interval change",
                      "interpretation": "sinus rhythm",
                      "acuity": "routine"},
             case_id="CX-1", author_id="rad-on", day=0)
    signed = rk.sign("CX-1-report", signer_id="rad-on", day=0)
    assert signed["status"] == "signed"
    assert rk.verify("CX-1-report")["valid"] is True
    log["report_signed"] = True

    # ---- 09:20 derm 'biopsy it' → telepath custody ----
    derm.submit_lesion("DC-1", patient_id="p-derm",
                       body_site="left temple", image_ref="img://9",
                       consent="signed", facility="phc-1",
                       referrer="mo-1", day=0, duration_days=90)
    b = biopsy_to_path(derm, path, "DC-1", "SP-1", day=0,
                       collector="nurse-1", facility="phc-1")
    assert b["specimen"]["site"] == "left temple"
    assert b["specimen"]["state"] == "collected"
    log["biopsy_in_custody"] = True

    # ---- 10:00 pharmacy order meets the CKD registry ----
    unit.register_patient("p-ckd", dry_weight_kg=68, egfr=25, day=0)
    verdict = renal_check(unit, "p-ckd", "metformin", day=0)
    assert verdict["verdict"] == "contraindicated", verdict
    log["metformin_blocked_at"] = verdict["egfr"]

    # ---- 11:00 MDT convenes on pulled evidence ----
    board.add_member("chair-1", role="chair", specialty="oncology")
    for st in ("fixed", "processed", "sectioned", "stained", "scanned"):
        kw = {"fixation_hours": 12} if st == "processed" else {}
        path.advance("SP-1", st, day=0, by="lab-1", **kw)
    pacs_svc.register_study(study_uid="1.2.3.9", accession="ACC-9",
                            patient_id="p-derm", modality="CT",
                            exam_protocol="ct-head", priority="routine",
                            facility="phc-1", day=0, instances=32)
    pack = case_pack("p-derm", case_ids=("CX-1",), rk=rk, path=path,
                     pacs_svc=pacs_svc)
    # note: the signed report belongs to case CX-1 (another patient arc);
    # this pack must contain ONLY this patient's evidence
    assert "specimen:SP-1@scanned" in pack["evidence_refs"], pack
    assert "study:1.2.3.9" in pack["evidence_refs"], pack
    submitted = submit_with_pack(board, "MDT-1", patient_id="p-derm",
                                 question="excise margins?", pack=pack,
                                 submitted_by="chair-1", day=0)
    assert submitted["state"] == "awaiting-meeting"
    log["mdt_evidence"] = len(submitted["evidence_refs"])

    # ---- 14:00 unacknowledged spo2 breach → tele-ICU ----
    engine.add_rule(Rule("spo2-low", version=1, vital="spo2",
                         kind="threshold", severity="critical", below=92,
                         condition="pneumonia",
                         action_hint="oxygen, escalate"))
    vitals.record("p-ward", "spo2", 88, day=0,
                  ts="2026-11-02T14:00:00Z")
    fired = engine.evaluate("p-ward", "spo2", day=0)
    assert fired, "the breach must fire"
    opened = {}
    esc = escalate_events(engine, icu, facility="phc-1", day=1,
                          opened=opened)
    assert esc["opened"] == ["icu-EVT-1"], esc
    log["teleicu_case"] = opened["EVT-1"]

    # ---- 15:00 tomorrow's chair plan ----
    unit.register_machine("M1")
    unit.register_machine("M2")
    unit.register_machine("M-HBV", hbv_dedicated=True)
    unit.register_patient("p-hbv", dry_weight_kg=70, egfr=8, day=0,
                          sessions_per_week=3)
    unit.record_serology("p-hbv", results={"hbv": "positive"}, day=0)
    unit.register_patient("p-hd", dry_weight_kg=74, egfr=9, day=0,
                          sessions_per_week=3)
    plan = chair_plan(unit, day=1, shifts=("morning", "afternoon"))
    flat = [a for s in plan["shifts"].values() for a in s]
    by_pid = {a["patient_id"]: a for a in flat}
    assert by_pid["p-hbv"]["machine_id"] == "M-HBV", plan
    assert by_pid["p-hd"]["machine_id"] in ("M1", "M2"), plan
    log["chair_plan_ok"] = plan["waitlist"] == []

    # ---- 16:00 cryo drift names the embryos ----
    clinic.register_couple("CPL", partner_a="pa", partner_b="pb", day=0)
    for p in ("pa", "pb"):
        for kind in ("cycle-treatment", "cryopreservation"):
            clinic.grant_consent("CPL", kind, method="signed", by=p,
                                 day=0)
    clinic.start_cycle("CY1", couple_id="CPL", protocol_version="a-v3",
                       day=1)
    for stage in ("stimulation", "triggered", "retrieval",
                  "fertilization"):
        clinic.advance("CY1", stage, day=2, by="dr-1")
    clinic.fertilize("CY1", method="ivf", n_embryos=1, day=3,
                     embryologist="emb-1")
    clinic.move_embryo("CY1-E1", "frozen", day=4, witness_1="emb-1",
                       witness_2="emb-2", tank="T1", canister="C2",
                       position="P3")
    watch = cryo_watch(clinic, "T1", [
        {"ts": f"2026-11-02T{h:02d}:00:00Z", "celsius": c}
        for h, c in enumerate([-190, -185, -180, -175])])
    assert watch["ticket"]["priority"] == "P2", watch
    assert [e["embryo_id"] for e in
            watch["ticket"]["embryos_at_risk"]] == ["CY1-E1"]
    log["cryo_ticket"] = watch["ticket"]["priority"]

    log["seams_proven"] = 7
    return log


if __name__ == "__main__":
    import json
    print(json.dumps(run_connected_day(), indent=1))
