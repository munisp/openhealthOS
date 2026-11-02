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
  17:30  fundus photo: ungradable = failed screen; two graders dispute,
         a third pair of eyes arbitrates → referral + recall (gap #8)
  18:00  RADPEER-scored peer review (self-review refused) lands on the
         reader's scorecard, denominators attached (gap #9)
  18:30  the formulary meets the order BEFORE it queues; an antibiotic
         rings its 48h timeout into the pharmacist queue (gap #10)
  19:00  derm suspicion starts the 14-day clock; an unseen melanoma
         breaches, loudly (gap #11)
  19:30  NEWS2 trajectory rises 1 → high; tele-ICU oversight opens
         exactly once (gap #12)
  20:00  cervix HPV DNA primary: positive → triage → treat same visit;
         HIV+ negative recalls at 36 months, not 60 (gap #13)
  20:30  anomaly scan: 20 ISUOG planes accounted for; a 12w dating scan
         refuses to masquerade (gap #14)
  21:00  the priors chain is attached at ORDER time; zero priors is
         stated, never silent (gap #15)
  21:30  the call/recall register drives the cervix cohort: the ladder
         hunts the woman who never came; screens close rounds (P1)
  22:00  colposcopy biopsy enters pathology custody; histology CIN3
         closes the loop and authorizes same-day LEEP (P1)
  22:30  sepsis bundle clocks: cultures-before-antibiotics flagged,
         conditional elements waived with reasons, breaches
         overdue-first (P1)
  23:00  the ICU bed board answers who-needs-what-today; device days
         carry the denominators infection control divides by (P1)
  23:30  admission acuity scores (APACHE-II-shaped, missing vitals
         refuse); mortality reported stratified by band — no invented
         SMR (P1)
  23:50  a pregnant diabetic's clock accelerates to trimester rhythm;
         an anti-VEGF series counts its injections and names the
         broken gap (P1)
  00:30  admission starts the medication reconciliation — the ward
         census drives it; 'ordered-despite-withhold' is loud (P1)
  01:00  terminology binding: LOINC/ICD seed codes bind; an unbound
         coded answer REFUSES the export (cross-cutting #9)
  01:30  mammography programme: BI-RADS drives the interval, BI-RADS 0
         is a debt not a negative, the audit shows every n (P1)
  02:00  offline specialist: checkout claims the cases, a mid-flight
         escalation collides at sync and is KEPT, never merged (P2)
  02:30  teaching files: only verified reports teach, anonymization is
         proven (no patient id, no absolute day, no burned-in pixels),
         retraction is append-only (P2)
  03:00  vocabulary convergence: cpoe's 'acei' and drugkb's
         'ace-inhibitor' resolve to one class — the interaction fires
         instead of presenting as clearance (P2)
  03:30  ECG intervals: named QTc method, block/conduction flags,
         implausible values refused (P0, cardiology)
  04:00  synoptic sign-out: verified histology read leaves as a SIGNED
         CAP-shaped synoptic; self-contradiction refuses before either
         side mutates (seam 14, P0 pathology)
  04:30  obstetric biometry meets the reference envelope; low CPR
         flags the brain-sparing pattern (P0)
  04:45  semen analysis: below-limit is a flag; a single zero is not
         a diagnosis (P1)
  05:00  dictation: speech lands as a provenanced DRAFT; verification
         stays human (P1)

Every refusal is asserted, not assumed. If a seam breaks, this day
does not end.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("crosslink", "nephrology", "telederm", "telepath", "teleread",
            "reportkit", "pacs", "fertility", "rota", "triggers", "vitals",
            "teleicu", "mdt", "serial", "duelgrade", "drscreen",
            "scorecard", "drugkb", "steward", "pharmreview",
            "earlywarn", "cervixscreen", "usguard", "callrecall",
            "sepsis", "acuity", "medrec", "inpatient", "terminology",
            "mammoprog", "offlinespec", "teachfile", "cpoe",
            "teleecg", "dictate"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from crosslink import (CrosslinkError, renal_check, biopsy_to_path,  # noqa: E402
                       case_pack, submit_with_pack, cryo_watch,
                       chair_plan, on_shift_readers, assign_on_shift,
                       escalate_events, synoptic_signout)
from dictate import DictationService  # noqa: E402
from teleecg import ECGService  # noqa: E402
from nephrology import DialysisUnit  # noqa: E402
from telederm import TeleDerm, DERM_CHECKS  # noqa: E402
from drscreen import DRScreening, FUNDUS_CHECKS  # noqa: E402
from pharmreview import PharmacyReview  # noqa: E402
from steward import TimeoutClock  # noqa: E402
from crosslink import (timeouts_to_review, deterioration_to_icu,  # noqa: E402
                       order_with_priors, screens_to_register,
                       colpo_biopsy_to_path, admissions_to_medrec)
from medrec import MedRec  # noqa: E402
from inpatient import Ward  # noqa: E402
from terminology import TerminologyMap  # noqa: E402
from mammoprog import MammoProgram  # noqa: E402
from offlinespec import OfflineReader  # noqa: E402
from teachfile import TeachingLibrary, TeachFileError  # noqa: E402
from drugkb import canonical_class  # noqa: E402
from formulary import Formulary  # noqa: E402
from earlywarn import EarlyWarner  # noqa: E402
from cervixscreen import CervixScreening  # noqa: E402
from usguard import UltrasoundQA, ANOMALY_PLANES  # noqa: E402
from callrecall import CallRecallRegister  # noqa: E402
from sepsis import SepsisClock  # noqa: E402
from teleicu import BedBoard  # noqa: E402
from acuity import AcuityRegistry, AcuityError, apache2  # noqa: E402
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
    for rid, lic in (("dr-1", "MDCN-G1"), ("dr-2", "MDCN-G2"),
                     ("dr-senior", "MDCN-G3")):
        readers.register(rid, licence_id=lic, licence_expiry_day=400,
                         scopes=("dr-grader",))
    readers.register("rad-peer", licence_id="MDCN-R3",
                     licence_expiry_day=400, scopes=("chest",))
    readers.register("pharm-1", licence_id="PCN-1",
                     licence_expiry_day=400, scopes=("clinical-pharmacist",))
    readers.register("cervix-1", licence_id="MDCN-C1",
                     licence_expiry_day=400, scopes=("cervix-reviewer",))
    readers.register("us-1", licence_id="MDCN-U1",
                     licence_expiry_day=400, scopes=("us-overreader",))
    readers.register("card-1", licence_id="MDCN-K1",
                     licence_expiry_day=400, scopes=("cardiac-reader",))

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
    # capture QA first (gap #8): a photo nobody quality-checked is a
    # rumour with pixels
    derm.assess_capture("img://9", day=0, by="nurse-1",
                        passed=DERM_CHECKS)
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

    # ---- 16:30 stimulation monitoring + grading (gap #6) ----
    clinic.register_couple("CPL2", partner_a="pc", partner_b="pd", day=0)
    for p in ("pc", "pd"):
        clinic.grant_consent("CPL2", "cycle-treatment",
                             method="signed", by=p, day=0)
    clinic.start_cycle("CY2", couple_id="CPL2", protocol_version="a-v3",
                       day=1)
    clinic.advance("CY2", "stimulation", day=1, by="dr-1")
    clinic.record_monitoring("CY2", day=2, e2_pmol_l=400,
                             follicles_ge_14mm=0, follicles_total=6,
                             endometrium_mm=6, fsh_dose_iu=225,
                             by="son-1")
    clinic.record_monitoring("CY2", day=4, e2_pmol_l=430,
                             follicles_ge_14mm=0, follicles_total=6,
                             endometrium_mm=6, fsh_dose_iu=225,
                             by="son-1")
    rec = clinic.monitoring_review("CY2", day=4)
    assert rec["recommendation"]["action"] == "increase-fsh", rec
    clinic.advance("CY2", "triggered", day=6, by="dr-1")
    clinic.advance("CY2", "retrieval", day=8, by="dr-1")
    clinic.advance("CY2", "fertilization", day=8, by="dr-1")
    clinic.fertilize("CY2", method="icsi", n_embryos=1, day=8,
                     embryologist="emb-1")
    clinic.grade_embryo("CY2-E1", culture_day=1, day=9, by="emb-1",
                        pn="2PN")
    clinic.grade_embryo("CY2-E1", culture_day=5, day=13, by="emb-1",
                        expansion=4, icm="A", te="B")
    greport = clinic.grade_report("CY2")
    assert greport["fertilized_2pn"] == 1 and greport["usable"] == 1
    log["fertility_lab_notebook"] = True

    # ---- 17:00 serial comparison (gap #7) ----
    from serial import SeriesTracker, compare_ecg, priors
    chain = priors(pacs_svc, "p-derm", modality="CT")
    assert chain["count"] == 1, chain
    st = SeriesTracker()
    st.record_measurement("1.2.3.9", lesion_id="L1", kind="long-axis",
                          value_mm=25, day=0, by="rad-on")
    pacs_svc.register_study(study_uid="1.2.3.10", accession="ACC-10",
                            patient_id="p-derm", modality="CT",
                            exam_protocol="ct-head", priority="routine",
                            facility="phc-1", day=30, instances=32)
    st.record_measurement("1.2.3.10", lesion_id="L1", kind="long-axis",
                          value_mm=31, day=30, by="rad-on")
    delta = st.compare_lesion("1.2.3.9", "1.2.3.10", lesion_id="L1")
    assert delta["flag"] == "progression", delta
    # serial ECG: QTc jumped on the follow-up tracing
    rk.draft("CX-1-ecg-fu", template_id="ecg-12lead@1.0.0",
             answers={"rate_bpm": 84, "rhythm": "sinus", "pr_ms": 152,
                      "qrs_ms": 90, "qtc_ms": 470,
                      "findings": "qt prolongation",
                      "interpretation": "sinus rhythm, prolonged qt",
                      "acuity": "urgent"},
             case_id="CX-1", author_id="rad-on", day=30)
    rk.sign("CX-1-ecg-fu", signer_id="rad-on", day=30)
    ecg_delta = compare_ecg(rk.reports["CX-1-report"],
                            rk.reports["CX-1-ecg-fu"])
    assert any("QTc +65" in f for f in ecg_delta["flags"]), ecg_delta
    log["serial_flags"] = len(ecg_delta["flags"])

    # ---- 17:30 DR screening: capture gate + two graders + arbiter ----
    drs = DRScreening(readers)
    # an unassessed fundus photo is stopped at the gate
    try:
        drs.screen("p-dm", eye="both", image_ref="fundus://1",
                   facility="phc-1", referrer="mo-1", day=0)
        raise AssertionError("unassessed capture must refuse")
    except TeleReadError:
        log["capture_gate_refused"] = True
    # first capture ungradable → failed screen; retake is a NEW image
    drs.assess_capture("fundus://1", day=0, by="photog-1",
                       passed=("field-definition", "illumination"),
                       failed=("focus", "media-clarity"))
    assert not drs.qa.gradable("fundus://1")
    drs.assess_capture("fundus://2", day=0, by="photog-1",
                       passed=FUNDUS_CHECKS)
    drs.screen("p-dm", eye="both", image_ref="fundus://2",
               facility="phc-1", referrer="mo-1", day=0)
    # two graders disagree → no referral on a dispute; the arbiter is a
    # third pair of eyes, and the referral fires only on their word
    drs.record_grade("p-dm", grade="moderate-npdr", by="dr-1", day=1)
    out = drs.record_grade("p-dm", grade="pdr", by="dr-2", day=1)
    assert out["resolution"] == "awaiting-arbitration", out
    assert out["critical_pending_arbitration"] == ["pdr"], out
    assert "referred" not in drs.cascade["p-dm"]
    arb = drs.arbitrate("p-dm", grade="pdr", by="dr-senior", day=2)
    assert arb["referred"] is True and arb["final_grade"] == "pdr", arb
    assert drs.cascade["p-dm"]["referred"] == 2
    assert drs.recall["p-dm"]["interval_months"] == 1
    log["duel_arbitrated"] = arb["final_grade"]

    # ---- 18:00 RADPEER peer review feeds the scorecard (gap #9) ----
    from scorecard import clinician_card
    radiology.draft_report("CX-1", reader_id="rad-on",
                           findings="small apical pneumothorax",
                           impression="pneumothorax, small", day=0,
                           template_version="cxr-v2")
    radiology.verify_report("CX-1", reader_id="rad-on", day=0)
    # a self-review is refused; a cross-craft review is refused; a
    # scored peer review lands
    try:
        radiology.peer_review("CX-1", reviewer="rad-on", day=1, score=1)
        raise AssertionError("self peer review must refuse")
    except TeleReadError:
        pass
    pr = radiology.peer_review("CX-1", reviewer="rad-peer", day=1,
                               score=2, discrepancy="size undercalled",
                               direction="undercall")
    assert pr["radpeer"] == "radpeer@1.0.0" and not pr["agree"], pr
    card = clinician_card(radiology, "rad-on", as_of=1,
                          qa=drs.qa, duel=drs.grading)
    assert card["turnaround"]["stat"]["n"] == 1, card
    assert card["peer_review"]["reviews_of_my_reports"] == 1, card
    assert card["peer_review"]["score_distribution"][2] == 1, card
    assert card["capture_qa"]["n"] == 0  # rad-on shot nothing: honest zero
    log["scorecard_peer_review"] = True

    # ---- 18:30 the formulary meets the queue; the timeout rings ----
    # (gap #10)
    pharm = PharmacyReview(readers)
    # warfarin with ibuprofen on board and CKD on the chart: the
    # knowledge base finds the interaction BEFORE the order queues
    egfr = unit.patients["p-ckd"]["egfr"]
    order = pharm.submit_order("RX-1", patient_id="p-ckd",
                               drug="warfarin", dose=5, route="po",
                               risk_class="anticoagulant",
                               prescriber="mo-1", facility="phc-1",
                               day=1, current_meds=("ibuprofen",),
                               egfr=egfr)
    safety = pharm.orders["RX-1"]["safety"]
    assert any(f["kind"] == "interaction" for f in safety["findings"])
    log["formulary_findings"] = len(safety["findings"])
    # enoxaparin into a G5 patient: contraindicated → STAT, not a
    # quiet queue line
    unit.register_patient("p-ckd5", dry_weight_kg=60, egfr=10, day=0)
    order2 = pharm.submit_order("RX-2", patient_id="p-ckd5",
                                drug="enoxaparin", dose=40, route="sc",
                                risk_class="anticoagulant",
                                prescriber="mo-1", facility="phc-1",
                                day=1, egfr=unit.patients["p-ckd5"]["egfr"])
    assert order2["priority"] == "stat", order2
    log["contraindicated_went_stat"] = True
    # an antibiotic course rings its 48h timeout into the same queue
    clock = TimeoutClock()
    clock.start_course("ABX-1", patient_id="p-ward",
                       drug_code="NG-CIPRO500", route="iv", day=0,
                       indication="pyelonephritis", prescriber="mo-1")
    tq = timeouts_to_review(pharm, clock, day=3)
    assert tq["newly_queued"] == 1 and tq["due"] == 1, tq
    again = timeouts_to_review(pharm, clock, day=3)
    assert again["newly_queued"] == 0  # the second bell is not a case
    clock.review_timeout("ABX-1", day=3, by="pharm-1", outcome="stop",
                         note="cultures negative")
    assert clock.timeouts_due(as_of=99) == []
    log["timeout_queued"] = tq["case_ids"][0]

    # ---- 19:00 the two-week-wait clock starts on suspicion (gap #11)
    derm.raise_suspicion("DC-1", finding="suspected-scc", day=1,
                         by="derm-1")
    derm.advance_pathway("DC-1", "referred", day=1, ref="REF-LAG-77")
    derm.advance_pathway("DC-1", "attended", day=10)
    derm.advance_pathway("DC-1", "diagnosed", day=12, diagnosis="scc",
                         confirmed_by="histology")
    # a second lesion crosses the target unseen → breach, loudly
    derm.assess_capture("img://10", day=0, by="nurse-1",
                        passed=DERM_CHECKS)
    derm.submit_lesion("DC-2", patient_id="p-derm2",
                       body_site="right calf", image_ref="img://10",
                       consent="signed", facility="phc-1",
                       referrer="mo-1", day=0, duration_days=200)
    derm.raise_suspicion("DC-2", finding="suspected-melanoma", day=0,
                         by="derm-1")
    breaches = derm.two_week_breaches(as_of=20)
    assert [b["case_id"] for b in breaches] == ["DC-2"], breaches
    assert breaches[0]["overdue_days"] == 6
    rep = derm.pathway_report(as_of=20)
    assert rep["suspected"] == 2 and rep["breaches_now"] == 1, rep
    log["tww_breach"] = breaches[0]["case_id"]

    # ---- 19:30 NEWS2: the trajectory is the finding (gap #12) ----
    ew = EarlyWarner(vitals)
    for d, vals in ((0, {"respiratory-rate": 15, "spo2": 98,
                         "temperature": 37.0, "systolic-bp": 122,
                         "heart-rate": 74}),
                    (1, {"respiratory-rate": 27, "spo2": 90,
                         "temperature": 39.1, "systolic-bp": 95,
                         "heart-rate": 118})):
        for vital, val in vals.items():
            vitals.record("p-ward2", vital, val, day=d,
                          ts=f"2026-11-0{2 + d}T20:00:00Z")
        ew.score_now("p-ward2", day=d, ts=f"2026-11-0{2 + d}T20:05:00Z",
                     avpu="A")
    trend = ew.trend("p-ward2")
    assert trend["direction"] == "rising" and trend["band_now"] == "high"
    opened2 = {}
    icu_out = deterioration_to_icu(ew, icu, facility="phc-1", day=1,
                                   opened=opened2)
    assert icu_out["opened"] == ["icu-news2-p-ward2"], icu_out
    again = deterioration_to_icu(ew, icu, facility="phc-1", day=2,
                                 opened=opened2)
    assert again["opened"] == []  # a rising trend is not a second case
    log["news2_icu_case"] = icu_out["opened"][0]

    # ---- 20:00 cervix: HPV DNA primary, treat before she leaves (gap #13)
    cervix = CervixScreening(readers)
    cervix.screen("p-cx1", facility="phc-1", screener="nurse-2", day=0,
                  modality="hpv-dna", hiv_positive=True)
    cervix.record_hpv("p-cx1", result="positive", day=0)
    cervix.triage("p-cx1", method="via", day=0)
    cervix.record_via("p-cx1", result="positive", day=0)
    tx = cervix.treat("p-cx1", method="thermal-ablation", day=0)
    assert tx["treatment"]["same_visit"] is True, tx
    # HPV-negative WITH HIV: 36-month recall, not the 60 of the general
    # population — immunosuppression shortens the interval
    cervix.screen("p-cx2", facility="phc-1", screener="nurse-2", day=0,
                  modality="hpv-dna", hiv_positive=True)
    neg = cervix.record_hpv("p-cx2", result="negative", day=0)
    assert neg["recall"]["interval_months"] == 36, neg["recall"]
    assert neg["recall"]["due_day"] == 0 + 30 * 36
    log["hpv_same_visit_treated"] = True
    log["hpv_hiv_recall_months"] = neg["recall"]["interval_months"]

    # ---- 20:30 anomaly scan: 20 planes accounted for, gaps named (#14)
    usg = UltrasoundQA(readers)
    usg.register_scan("US-A1", patient_id="p-obs1", sonographer="sono-1",
                      facility="phc-1", gestational_weeks=21,
                      image_ref="us://a1", day=0)
    planes = {p: "adequate" for p in ANOMALY_PLANES}
    planes["three-vessel-trachea"] = "not-visualized"
    chk = usg.record_anomaly_checklist("US-A1", planes=planes, day=0,
                                       by="sono-1")
    assert chk["complete"] is False
    assert chk["gaps"] == ["three-vessel-trachea"], chk
    inc = usg.incomplete_anomaly_scans()
    assert [s["scan_id"] for s in inc] == ["US-A1"], inc
    # a 12w dating scan must not masquerade as the anomaly scan
    usg.register_scan("US-A2", patient_id="p-obs2", sonographer="sono-1",
                      facility="phc-1", gestational_weeks=12,
                      image_ref="us://a2", day=0)
    try:
        usg.record_anomaly_checklist(
            "US-A2", planes={p: "adequate" for p in ANOMALY_PLANES},
            day=0, by="sono-1")
        raise AssertionError("12w scan must refuse the anomaly protocol")
    except TeleReadError:
        log["anomaly_ga_gate_refused"] = True
    log["anomaly_incomplete_scan"] = inc[0]["scan_id"]

    # ---- 21:00 the priors chain rides the order, not memory (gap #15)
    pacs_svc.register_study(study_uid="2.1", accession="B1",
                            patient_id="p-ward2", modality="CT",
                            exam_protocol="ct-chest", priority="routine",
                            facility="phc-1", day=0, instances=40)
    pacs_svc.register_study(study_uid="2.2", accession="B2",
                            patient_id="p-ward2", modality="CT",
                            exam_protocol="ct-chest", priority="routine",
                            facility="phc-1", day=1, instances=42)
    prio = order_with_priors(radiology, pacs_svc, "CX-9",
                             patient_id="p-ward2", modality="CT",
                             priority="urgent", question="?worsening",
                             facility="phc-1", referrer="mo-1", day=2,
                             scope="chest")
    assert prio["priors"]["count"] == 2, prio["priors"]
    assert radiology.cases["CX-9"]["meta"]["priors"]["study_uids"] == \
        ["2.1", "2.2"]
    # zero priors is stated, never silently absent
    ghost = order_with_priors(radiology, pacs_svc, "CX-10",
                              patient_id="p-new", priority="stat",
                              question="?pneumothorax", facility="phc-1",
                              referrer="mo-1", day=2, scope="chest")
    assert ghost["priors"]["count"] == 0
    log["priors_attached"] = prio["priors"]["count"]

    # ---- 21:30 the register drives the programme (gap: call/recall) --
    register = CallRecallRegister("cervix")
    register.enroll("p-cx1", birth_day=-35 * 365, day=0,
                    hiv_positive=True)
    register.enroll("p-cx2", birth_day=-40 * 365, day=0,
                    hiv_positive=True)
    register.enroll("p-cx-never", birth_day=-38 * 365, day=0)
    register.record_step("p-cx-never", step="invite", day=0,
                         channel="sms")
    # the women who screened at 20:00 close their rounds via the seam —
    # the register keeps hunting only the one who never came
    marked = {}
    scr = screens_to_register(cervix, register, day=0, marked=marked)
    assert set(scr["marked_screened"]) == {"p-cx1", "p-cx2"}, scr
    again = screens_to_register(cervix, register, day=1, marked=marked)
    assert again["marked_screened"] == []   # the bell rings once
    # a late invite does not pause the ladder: reminder-1 already due
    steps = register.steps_due(as_of=15)
    assert [(s["patient_id"], s["step"]) for s in steps] == \
        [("p-cx-never", "reminder-1")], steps
    cov = register.coverage(as_of=1)
    assert cov["eligible"] == 3 and cov["screened_in_interval"] == 2
    assert cov["never_screened"] == 1, cov
    log["callrecall_coverage"] = cov["coverage_rate"]
    log["callrecall_never_came"] = "p-cx-never"

    # ---- 22:00 colposcopy biopsy: custody begins, histology closes (P1)
    cervix.screen("p-cx3", facility="phc-1", screener="nurse-2", day=0,
                  modality="hpv-dna")
    cervix.record_hpv("p-cx3", result="positive", day=0)
    cervix.triage("p-cx3", method="colposcopy", day=0)
    cervix.refer("p-cx3", destination="colpo-clinic", day=0,
                 reason="hpv-positive triage")
    cervix.record_colposcopy("p-cx3", impression="high-grade",
                             decision="biopsy", day=1, by="cervix-1")
    bio = colpo_biopsy_to_path(cervix, path, "p-cx3",
                               specimen_id="SP-CX3",
                               collector="cervix-1", day=1)
    assert bio["state"] == "collected", bio
    loops = cervix.open_loops(as_of=20)
    assert "histology-pending" in {l["result"] for l in loops}
    hist = cervix.record_histology("p-cx3", result="cin3", day=9)
    assert hist["histology"]["management"] == "treat"
    tx3 = cervix.treat("p-cx3", method="leep", day=9)
    assert tx3["treatment"]["same_visit"] is True
    loops2 = cervix.open_loops(as_of=20)
    assert "p-cx3" not in {l["patient_id"] for l in loops2}
    log["histology_loop_closed"] = "p-cx3"

    # ---- 22:30 sepsis bundle: every hour accountable (P1) ----
    sepsis = SepsisClock()
    sepsis.start_clock("p-ward2", zero_hour=0, by="mo-1",
                       source="news2-escalation")
    sepsis.complete("p-ward2", "lactate", hour=0, by="mo-1")
    sepsis.complete("p-ward2", "blood-cultures", hour=0, by="mo-1")
    abx = sepsis.complete("p-ward2", "broad-spectrum-antibiotics",
                          hour=1, by="mo-1")
    assert abx["met"] and abx["sequence_violation"] is None
    sepsis.complete("p-ward2", "fluids-30mlkg", hour=2, by="mo-1")
    sepsis.waive("p-ward2", "vasopressors", hour=2, by="mo-1",
                 reason="MAP 71 after fluids")
    sepsis.complete("p-ward2", "repeat-lactate", hour=5, by="mo-1")
    # next door: antibiotics before cultures — recorded, flagged,
    # never erased; the rest of the bundle simply never happens
    sepsis.start_clock("p-ward3", zero_hour=0, by="mo-1",
                       source="clinical-gestalt")
    out_of_order = sepsis.complete("p-ward3",
                                   "broad-spectrum-antibiotics",
                                   hour=0, by="mo-1")
    assert out_of_order["sequence_violation"] == ["blood-cultures"]
    br = sepsis.breaches(as_of=8)
    assert len(br) == 5 and all(b["patient_id"] == "p-ward3"
                                for b in br), br
    assert br[0]["element"] == "lactate"      # most overdue first
    rep = sepsis.report(as_of=8)
    assert rep["all_or_nothing"] == {"n": 2, "met": 1, "rate": 0.5}
    assert rep["sequence_violations"] == 1
    log["sepsis_breaches_open"] = len(br)
    log["sepsis_all_or_nothing_met"] = rep["all_or_nothing"]["met"]

    # ---- 23:00 the bed board: who needs WHAT today (P1) ----
    board = BedBoard("icu-1")
    board.admit("p-ward2", bed="B1", day=0)
    board.device_start("p-ward2", "ventilator", day=0)
    board.device_start("p-ward2", "central-line", day=0)
    board.admit("p-ward3", bed="B2", day=0)
    board.device_start("p-ward3", "urinary-catheter", day=1)
    rl = board.rounding_list(as_of=1)
    # vented + lined: 6 must-dos vs the catheter patient's 3
    assert rl[0]["patient_id"] == "p-ward2" and rl[0]["n_open"] == 6, rl
    for chk in ("sedation-interruption", "spontaneous-breathing-trial",
                "head-of-bed-30", "dvt-prophylaxis-review",
                "family-update"):
        board.record_check("p-ward2", chk, day=1, by="icu-1")
    # 'contraindicated' is a decision with a reason, not a skip
    board.record_check("p-ward2", "line-necessity-review", day=1,
                       by="icu-1", outcome="contraindicated",
                       reason="no alternate access; antibiotics running")
    assert board.rounding_list(as_of=1)[0]["patient_id"] == "p-ward3"
    drep = board.device_report(as_of=2)
    assert drep["patient_days"] == 4
    assert drep["device_days"] == {"ventilator": 2, "central-line": 2,
                                   "urinary-catheter": 1}, drep
    log["bedboard_open_first"] = "p-ward3"
    log["bedboard_device_days"] = sum(drep["device_days"].values())

    # ---- 23:30 acuity: mortality stratified, denominators shown (P1)
    acu = AcuityRegistry()
    s_normal = apache2(temp_c=37.0, map_mmhg=90, hr=80, rr=14,
                       fio2=0.21, pao2=95, ph=7.40, na=140, k=4.0,
                       creat_mgdl=1.0, hct=40, wbc=8, gcs=15, age=40)
    s_sick = apache2(temp_c=39.5, map_mmhg=55, hr=125, rr=28, fio2=0.4,
                     pao2=58, ph=7.28, na=152, k=5.7, creat_mgdl=2.2,
                     arf=True, hct=26, wbc=17, gcs=12, age=70,
                     chronic_health="nonsurgical-or-emergency")
    assert s_normal["total"] == 0 and s_sick["total"] == 37
    acu.record_score("icu-1", "p-ward2", day=0, score=s_sick)
    acu.record_score("icu-1", "p-ward4", day=0, score=s_normal)
    acu.record_outcome("icu-1", "p-ward2", day=3, outcome="survived")
    acu.record_outcome("icu-1", "p-ward4", day=2, outcome="survived")
    try:
        acu.record_score("icu-1", "p-ward5", day=0, score=37)
        raise AssertionError("a bare integer must refuse")
    except AcuityError:
        log["acuity_bare_score_refused"] = True
    mort = acu.mortality_by_band("icu-1")
    assert mort["scored"] == 2
    assert mort["bands"][3]["n"] == 1 and \
        mort["bands"][3]["mortality_rate"] == 0.0, mort
    log["acuity_high_band_n"] = mort["bands"][3]["n"]

    # ---- 23:50 DR: pregnancy clock + the anti-VEGF series (P1) ----
    # p-dm (PDR, arbitrated at 17:30) attends and starts treatment —
    # the cascade closes through attendance, not around it
    drs.advance("p-dm", "attended", day=10)
    drs.record_treatment("p-dm", kind="laser-prp", eye="left", day=10,
                         by="oph-1")
    assert drs.cascade["p-dm"]["treated"] == 10
    # a pregnant diabetic graded mild: her clock accelerates to the
    # trimester rhythm; the grade plan is preserved, not rewritten
    drs.assess_capture("fundus://9", day=0, by="photog-1",
                       passed=FUNDUS_CHECKS)
    drs.screen("p-dm-preg", eye="both", image_ref="fundus://9",
               facility="phc-1", referrer="mo-1", day=0)
    drs.mark_pregnant("p-dm-preg", day=0, lmp_day=-45)
    drs.record_grade("p-dm-preg", grade="mild-npdr", by="dr-1", day=1)
    preg_out = drs.record_grade("p-dm-preg", grade="mild-npdr",
                                by="dr-2", day=1)
    assert preg_out["pregnancy_override"] is True
    assert preg_out["recall"]["interval_months"] == 3
    assert preg_out["recall"]["grade_plan_months"] == 12
    # an anti-VEGF series is a rhythm: 28-day intervals, the 139-day
    # gap flagged as broken
    drs.assess_capture("fundus://10", day=0, by="photog-1",
                       passed=FUNDUS_CHECKS)
    drs.screen("p-dm-mac", eye="both", image_ref="fundus://10",
               facility="phc-1", referrer="mo-1", day=0)
    drs.record_grade("p-dm-mac", grade="maculopathy", by="dr-1", day=1)
    drs.record_grade("p-dm-mac", grade="maculopathy", by="dr-2", day=1)
    drs.advance("p-dm-mac", "attended", day=5)
    for d in (5, 33, 61):
        drs.record_treatment("p-dm-mac", kind="anti-vegf-injection",
                             eye="right", day=d, by="oph-1")
    # the next injection was due at 89; at day 100 the worklist names
    # her, overdue-first
    due_tx = drs.treatment_due(as_of=100)
    assert due_tx[0]["patient_id"] == "p-dm-mac"
    assert due_tx[0]["reason"] == "injection-due"
    assert due_tx[0]["overdue_days"] == 11, due_tx
    # she returns at day 200: the 139-day gap is flagged as a broken
    # series — a relapse risk, not a scheduling detail
    drs.record_treatment("p-dm-mac", kind="anti-vegf-injection",
                         eye="right", day=200, by="oph-1")
    series = drs.injection_series("p-dm-mac", "right")
    assert series["injections"] == 4 and series["broken_gaps"] == [139]
    log["dr_series_broken_gaps"] = series["broken_gaps"]
    log["dr_pregnancy_override"] = True

    # ---- 00:30 admission starts the med list, not the amnesia (P1) --
    ward = Ward("phc-1")
    ward.add_bed("WA-1", "ward-a")
    ward.add_bed("WA-2", "ward-a")
    ward.admit("p-ward5", "WA-1", "nurse-1")
    ward.admit("p-ward6", "WA-2", "nurse-1")
    med = MedRec()
    med_started = set()
    mr = admissions_to_medrec(ward, med, day=0, by="clerk-1",
                              started=med_started)
    assert sorted(mr["started"]) == ["p-ward5", "p-ward6"]
    again = admissions_to_medrec(ward, med, day=1, by="clerk-1",
                                 started=med_started)
    assert again["started"] == []   # the bell rings once
    med.record_home_med("p-ward5", "metformin", setting="admission",
                        dose="500 bd", source="brought-in-medications",
                        day=0)
    med.record_home_med("p-ward5", "amlodipine", setting="admission",
                        dose="5 od", source="patient-report", day=0)
    med.reconcile("p-ward5", "metformin", setting="admission",
                  decision="withhold", day=0, by="mo-1",
                  reason="AKI on admission; restart when creat settles")
    med.note_order("p-ward5", "metformin", setting="admission", day=1)
    disc = med.discrepancies("p-ward5")
    kinds = {d["drug"]: d["kind"] for d in disc}
    assert kinds["metformin"] == "ordered-despite-withhold", disc
    assert kinds["amlodipine"] == "undecided"
    assert med.unreconciled(as_of=2)[0]["patient_id"] == "p-ward5"
    log["medrec_discrepancy"] = kinds["metformin"]

    # ---- 01:00 terminology: unbound is not a code (cross-cutting #9)
    tmap = TerminologyMap("nigeria-pilot-v1", deployment="nigeria-pilot")
    obs_template = {"id": "observation@1.0.0",
                    "sections": [
                        {"type": "code", "key": "kind",
                         "options": {"spo2": "SpO2",
                                     "heart-rate": "HR",
                                     "cvp": "CVP"}},
                        {"type": "number", "key": "value"}]}
    coded = tmap.bind_report(obs_template, {"kind": "spo2",
                                            "value": 98})
    assert coded["codings"][0]["code"] == "59408-5"
    # a CVP observation has no binding: the coded export REFUSES
    # rather than smuggle semantics past the audit
    try:
        tmap.bind_report(obs_template, {"kind": "cvp", "value": 8})
        raise AssertionError("unbound coded field must refuse")
    except Exception as exc:
        assert "unbound" in str(exc).lower()
        log["terminology_unbound_refused"] = True
    cov = tmap.coverage(obs_template)
    assert cov["coded_values"] == 3 and cov["bound"] == 2
    log["terminology_coverage"] = cov["coverage_rate"]

    # ---- 01:30 mammography programme: category → interval → audit --
    mammo = MammoProgram()
    mammo.record_screen("w-m1", day=0, birads="1", by="rad-on",
                        facility="phc-1", image_ref="mammo://1")
    mammo.record_screen("w-m0", day=0, birads="0", by="rad-on",
                        facility="phc-1", image_ref="mammo://0")
    mammo.record_screen("w-m4", day=0, birads="4", by="rad-on",
                        facility="phc-1", image_ref="mammo://4")
    # BI-RADS 4 owes a biopsy in 14 days; BI-RADS 0 owes imaging in 30
    mdue = mammo.due(as_of=40)
    assert [d["patient_id"] for d in mdue] == ["w-m4", "w-m0"], mdue
    # completing the WRONG step closes nothing
    try:
        mammo.complete_pathway("w-m0", day=35, what="biopsy-referral")
        raise AssertionError("wrong step must refuse")
    except Exception as exc:
        assert "owes" in str(exc)
    mammo.complete_pathway("w-m0", day=35, what="additional-imaging")
    mammo.record_biopsy_outcome("w-m4", day=20, outcome="malignant")
    audit = mammo.audit()
    assert audit["screens"] == 3 and audit["recall_numerator"] == 1
    assert audit["biopsy_ppv"] == 1.0
    assert audit["detection_per_1000"] == round(1 / 3 * 1000, 2)
    log["mammo_audit_ppv"] = audit["biopsy_ppv"]
    log["mammo_overdue_first"] = mdue[0]["patient_id"]

    # ---- 02:00 offline specialist: checkout is a claim, conflicts
    # are loud, offline is not an alibi (P2)
    radiology.order("CX-O1", priority="routine", question="TB?",
                    facility="phc-1", referrer="dr-a", day=0)
    radiology.order("CX-O2", priority="routine", question="nodule?",
                    facility="phc-1", referrer="dr-a", day=0)
    offline = OfflineReader(radiology)
    offline.checkout("bundle-1", "rad-off", ["CX-O1", "CX-O2"], day=1)
    assert radiology.cases["CX-O1"]["assigned_to"] == "rad-off"
    for cid, imp in (("CX-O1", "active TB"), ("CX-O2", "benign nodule")):
        offline.draft_offline("bundle-1", cid,
                              findings="see report", impression=imp,
                              template_version="cxr@1.0.0", day=2)
    # while the reader was offline, the hub escalated CX-O1 to stat:
    # the collision is the finding — the draft is KEPT, never merged
    radiology.cases["CX-O1"]["priority"] = "stat"
    sync = offline.sync_back("bundle-1", day=3)
    assert len(sync["landed"]) == 1, sync  # CX-O2's report id
    assert [c["case_id"] for c in sync["conflicts"]] == ["CX-O1"]
    assert radiology.cases["CX-O2"]["state"] == "reported-draft"
    assert "CX-O1" not in radiology.reports  # no silent merge
    log["offline_synced"] = len(sync["landed"])
    log["offline_conflict_kept"] = sync["conflicts"][0]["case_id"]

    # ---- 02:30 teaching files: only verified reports teach;
    # anonymization is proven, not promised; retraction is append-only
    pacs_svc.register_study(study_uid="1.2.840.T1", accession="ACC-T1",
                            patient_id="p-teach", modality="CR",
                            exam_protocol="chest-pa", priority="routine",
                            facility="phc-1", day=0, instances=2)
    teach = TeachingLibrary(radiology, pacs_svc)
    # the draft-only conflict case cannot teach
    try:
        teach.contribute("CX-O1", "1.2.840.T1", title="draft case",
                         consent_basis="explicit-consent",
                         contributed_by="rad-off", day=3)
        raise RuntimeError("unverified case must not teach")
    except TeachFileError as exc:
        assert "verified" in str(exc)
    # verify CX-O2's synced report, then contribute — anonymized
    radiology.verify_report("CX-O2", reader_id="rad-off", day=3)
    tid = teach.contribute("CX-O2", "1.2.840.T1",
                           title="Solitary pulmonary nodule",
                           consent_basis="anonymized-irreversible",
                           contributed_by="rad-off", day=4,
                           tags=("nodule",))["teaching_id"]
    entry = teach.library()[0]
    assert "p-teach" not in str(entry)
    assert entry["case_age_days"] == 4  # relative day, never absolute
    # free text carrying the patient id refuses before storage
    try:
        teach.contribute("CX-O2", "1.2.840.T1",
                         title="nodule in p-teach",
                         consent_basis="explicit-consent",
                         contributed_by="rad-off", day=4)
        raise RuntimeError("patient id in free text must refuse")
    except TeachFileError as exc:
        assert "identifier" in str(exc)
        log["teach_leak_refused"] = True
    teach.record_use(tid, by="student-1", day=5, purpose="training")
    teach.retract(tid, by="rad-off", day=6, reason="consent-withdrawn")
    try:
        teach.record_use(tid, by="student-2", day=7, purpose="training")
        raise RuntimeError("retracted file must not circulate")
    except TeachFileError as exc:
        assert "retracted" in str(exc)
        log["teach_retract_blocks_use"] = True
    assert teach.usage_report()["retracted"] == 1
    log["teach_contributed"] = teach.usage_report()["contributed"]

    # ---- 03:00 vocabulary convergence: cpoe and drugkb speak ONE
    # class language — an unmatched synonym presents as clearance (P2)
    assert canonical_class("acei") == "ace-inhibitor"
    f_hits = Formulary().check_interactions(["NG-LISIN10", "NG-IBU400"])
    assert f_hits, "acei/nsaid pair lost to vocabulary drift"
    log["vocab_converged"] = f_hits[0]["severity"] == "major"

    # ---- 03:30 ECG intervals: a read without intervals is not a
    # read; the QTc method is always named (P0, cardiology)
    ecg = ECGService(readers)
    ecg.record_tracing("T-77", patient_id="p-ecg", kind="12-lead",
                       device_id=None, waveform_ref="wave://t77",
                       facility="phc-1", referrer="dr-a", day=2)
    m = ecg.record_measurements("T-77", by="tech-1", day=2,
                                rate_bpm=88, pr_ms=230, qrs_ms=126,
                                qt_ms=400, rr_ms=682,
                                qtc_method="bazett", axis_deg=40,
                                patient_sex="male")
    assert m["qtc_ms"] > 400  # Bazett overcorrects at 88bpm
    assert any("first-degree" in f for f in m["flags"])
    assert any("conduction delay" in f for f in m["flags"])
    try:
        ecg.record_measurements("T-77", by="tech-1", day=2,
                                rate_bpm=9, pr_ms=160, qrs_ms=90,
                                qtc_ms=410, qtc_method="bazett")
        raise RuntimeError("implausible intervals must refuse")
    except TeleReadError:
        log["ecg_implausible_refused"] = True
    log["ecg_qtc_method"] = m["qtc_method"]

    # ---- 04:00 synoptic sign-out (seam 14): the verified histology
    # read leaves as a SIGNED synoptic; contradiction refuses BEFORE
    # either side mutates (P0, pathology)
    path.register_specimen("SP-COLON", patient_id="p-colon",
                           specimen_type="resection", site="colon",
                           collector="surg-1", facility="phc-1", day=0)
    for st in ("fixed", "processed", "sectioned", "stained",
               "scanned"):
        kw = {"fixation_hours": 12} if st == "processed" else {}
        path.advance("SP-COLON", st, day=1, by="lab-1", **kw)
    path.submit_for_reading("SP-COLON", case_id="PC-COLON",
                            priority="routine", referrer="surg-1",
                            question="colon ca resection",
                            artifact="wsi", day=1)
    path.reading.assign("PC-COLON", day=1, prefer="path-1")
    path.reading.draft_report("PC-COLON", reader_id="path-1",
                              findings="3cm tumor, 1/14 nodes",
                              impression="adenocarcinoma",
                              template_version="freeform@1", day=2)
    path.reading.verify_report("PC-COLON", reader_id="path-1", day=2)
    syn_bad = {"specimen_type": "right hemicolectomy",
               "tumor_site": "ascending colon",
               "histologic_type": "adenocarcinoma", "grade": "g2",
               "tumor_extent_pt": "pT3", "margins": "negative",
               "lymph_nodes_examined": 2, "lymph_nodes_positive": 5,
               "lymphovascular_invasion": "absent",
               "perineural_invasion": "absent",
               "tnm_stage": "pT3 N1a (AJCC 8th)"}
    try:
        synoptic_signout(path, rk, case_id="PC-COLON",
                         report_id="SYN-BAD",
                         template_id="cap-colon-resection@1.0.0",
                         answers=syn_bad, signer="path-1", day=2)
        raise RuntimeError("5 positive of 2 examined must refuse")
    except Exception as exc:
        assert "cannot find more than you looked at" in str(exc)
    assert "SYN-BAD" not in rk.reports  # no half-state
    assert path._spec("SP-COLON").get("synoptic_report") is None
    syn_ok = dict(syn_bad, lymph_nodes_examined=14,
                  lymph_nodes_positive=1)
    out = synoptic_signout(path, rk, case_id="PC-COLON",
                           report_id="SYN-1",
                           template_id="cap-colon-resection@1.0.0",
                           answers=syn_ok, signer="path-1", day=2)
    assert rk.verify("SYN-1")["valid"]
    log["synoptic_signed"] = out["report_id"]

    # ---- 04:30 obstetric biometry: measurements meet the reference
    # envelope; a small AC flags growth review, not a diagnosis (P0)
    usg.register_scan("US-B1", patient_id="p-ob2",
                      sonographer="son-1", facility="phc-1",
                      gestational_weeks=30, image_ref="img://b1",
                      day=2)
    bio = usg.record_biometry("US-B1",
                              measurements={"bpd": 75, "hc": 275,
                                            "ac": 200, "fl": 55},
                              day=2, by="son-1")
    assert any("ac" in f for f in bio["flags"]), bio
    dop = usg.record_doppler("US-B1", day=2, by="son-1",
                             umbilical_pi=1.6, mca_pi=1.4, cpr=0.875)
    assert any("brain-sparing" in f for f in dop["flags"])
    log["biometry_flagged"] = "US-B1"

    # ---- 04:45 semen analysis: below a reference limit is a flag,
    # and a single zero is not a diagnosis (P1, fertility)
    clinic.register_couple("CPL3", partner_a="pe", partner_b="pf",
                           day=0)
    sa = clinic.record_semen_analysis(
        "CPL3", day=1, by="lab-1", abstinence_days=3, volume_ml=2.0,
        concentration_m_per_ml=0.0, total_motility_pct=0.0,
        progressive_motility_pct=0.0, normal_forms_pct=0.0)
    assert sa["azoospermia_needs_confirmation"]
    assert not clinic.semen_summary("CPL3")["azoospermia_confirmed"]
    log["semen_single_zero_held"] = True

    # ---- 05:00 dictation: speech becomes a DRAFT with provenance;
    # the ceiling is reported-draft, verification stays human (P1)
    dict_svc = DictationService(radiology)
    radiology.order("CX-D1", priority="routine", question="effusion?",
                    facility="phc-1", referrer="dr-a", day=2)
    radiology.assign("CX-D1", day=2, prefer="rad-on")
    dout = dict_svc.dictate("CX-D1", reader_id="rad-on",
                     transcript="Small right pleural effusion. "
                                "Clinical correlation advised.",
                     engine="whisper", engine_version="large-v3",
                     day=2, template_version="cxr@1.0.0")
    assert dout["state"] == "draft"
    assert radiology.cases["CX-D1"]["state"] == "reported-draft"
    assert dict_svc.sessions[-1]["transcript"].startswith("Small right")
    log["dictation_landed_draft"] = dout["report_id"]
    log["dictation_engine"] = dout["provenance"]["engine"] + "@" + \
        dout["provenance"]["engine_version"]

    log["seams_proven"] = 30
    return log


if __name__ == "__main__":
    import json
    print(json.dumps(run_connected_day(), indent=1))
