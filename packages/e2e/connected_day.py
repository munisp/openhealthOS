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
  05:30  the board's treat decision becomes real screened orders;
         an allergy kills the batch before anything drafts (seam 15)
  05:45  TNM staging: the edition is part of the stage; restaging
         never erases the first stage
  06:00  frozen section: own clock, documented read-back, paraffin
         concordance with denominators
  06:15  blocks and slides are physical assets with a location trail
  06:30  reflex IHC ordered from the verified report returns as an
         addendum — never an orphan result

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
            "teleecg", "dictate", "staging", "viewerlaunch",
            "devicehub", "travelpass", "payrails", "ips",
            "surveillance", "poe_screen"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from crosslink import (CrosslinkError, renal_check, biopsy_to_path,  # noqa: E402
                       case_pack, submit_with_pack, cryo_watch,
                       chair_plan, on_shift_readers, assign_on_shift,
                       escalate_events, synoptic_signout,
                       decision_to_orders)
from dictate import DictationService  # noqa: E402
from teleecg import ECGService  # noqa: E402
from staging import StagingRegistry  # noqa: E402
from orders import OrderService  # noqa: E402
from teleecg import HolterService, MachineEditTrail  # noqa: E402
from nephrology import adequacy_from_labs, VaccinationBook  # noqa: E402
from fertility import registry_return as fert_registry_return  # noqa: E402
from fertility import FertilityError  # noqa: E402
from telederm import LesionMap  # noqa: E402
from viewerlaunch import ViewerLauncher  # noqa: E402
from crosslink import verify_with_pixels  # noqa: E402
from crosslink import (imported_case_signal, poe_entry_screen,  # noqa: E402
                       clear_public_health_hold)
from teleicu import FamilyLog  # noqa: E402
from drugkb import check_iv_y_site, tpn_assess  # noqa: E402
from serial import growth_assessment  # noqa: E402
from devicehub import (DeviceHub, DeviceHubError,  # noqa: E402
                       parse_oru as parse_oru_hl7)
from travelpass import TravelPassRegistry, TravelPassError  # noqa: E402
from travelpass import (ips_document, emr_encounter,  # noqa: E402
                        notification_line, settle, refund_early_exit)
from payrails import RailRouter  # noqa: E402
from telepath import (FrozenSectionLog, DerivativeInventory,  # noqa: E402
                      ReflexLog)
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
from cervixscreen import CervixScreening, DeviceQA  # noqa: E402
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
    # device treatment gates on the QA book: the probe that was not
    # checked today is a room heater with ambitions
    cervix.device_qa = DeviceQA()
    cervix.device_qa.register_device("TA-1", kind="thermal-ablation",
                                     facility="phc-1")
    try:
        cervix.treat("p-cx1", method="thermal-ablation", day=0,
                     device_id="TA-1")
        raise RuntimeError("treated with an un-QA'd device")
    except TeleReadError as exc:
        assert "QA" in str(exc), exc
        log["cervix_device_qa_refused"] = True
    cervix.device_qa.record_qa("TA-1",
                               checks={"temp-probe-calibrated": True,
                                       "tip-integrity": True,
                                       "timer-functional": True},
                               by="nurse-2", day=0)
    tx = cervix.treat("p-cx1", method="thermal-ablation", day=0,
                      device_id="TA-1")
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
    beds = BedBoard("icu-1")
    beds.admit("p-ward2", bed="B1", day=0)
    beds.device_start("p-ward2", "ventilator", day=0)
    beds.device_start("p-ward2", "central-line", day=0)
    beds.admit("p-ward3", bed="B2", day=0)
    beds.device_start("p-ward3", "urinary-catheter", day=1)
    rl = beds.rounding_list(as_of=1)
    # vented + lined: 6 must-dos vs the catheter patient's 3
    assert rl[0]["patient_id"] == "p-ward2" and rl[0]["n_open"] == 6, rl
    for chk in ("sedation-interruption", "spontaneous-breathing-trial",
                "head-of-bed-30", "dvt-prophylaxis-review",
                "family-update"):
        beds.record_check("p-ward2", chk, day=1, by="icu-1")
    # 'contraindicated' is a decision with a reason, not a skip
    beds.record_check("p-ward2", "line-necessity-review", day=1,
                       by="icu-1", outcome="contraindicated",
                       reason="no alternate access; antibiotics running")
    assert beds.rounding_list(as_of=1)[0]["patient_id"] == "p-ward3"
    drep = beds.device_report(as_of=2)
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

    # ---- 05:30 the board's treat decision becomes real orders
    # (seam 15): screened, placed, traced back onto the case — a
    # decision nobody executes is an open loop (P1, MDT)
    board.add_member("onc-1", role="member", specialty="oncology")
    board.add_member("rad-1", role="member", specialty="radiology")
    board.hold_meeting("M-1", day=2, chair="chair-1",
                       attendees=["chair-1", "onc-1", "rad-1"],
                       case_ids=["MDT-1"])
    board.record_decision("M-1", "MDT-1", day=2,
                          decision="wide excision + adjuvant "
                                   "analgesia protocol",
                          actions=["start analgesia", "book theatre"],
                          recorder="onc-1")
    cpoe = OrderService()
    out = decision_to_orders(
        board, cpoe, "MDT-1", prescriber_id="onc-1", day=2,
        prescriptions=[("NG-PARA500", 1000, 3, 5)])
    assert cpoe.orders[out["orders"][0]].status == "placed"
    # an allergy kills the whole batch BEFORE anything drafts — on a
    # separate decided case (the first already carries its orders)
    board.submit_case("MDT-2", patient_id="p-derm2",
                      question="adjuvant antibiotics?",
                      evidence_refs=submitted["evidence_refs"],
                      submitted_by="chair-1", day=2)
    board.hold_meeting("M-2", day=2, chair="chair-1",
                       attendees=["chair-1", "onc-1", "rad-1"],
                       case_ids=["MDT-2"])
    board.record_decision("M-2", "MDT-2", day=2,
                          decision="adjuvant amoxicillin course",
                          actions=["start amoxicillin"],
                          recorder="onc-1")
    allergic_orders = OrderService()
    try:
        decision_to_orders(
            board, allergic_orders, "MDT-2", prescriber_id="onc-1",
            day=3, prescriptions=[("NG-AMOX500", 500, 3, 7)],
            patient_allergies=["penicillin"])
        raise RuntimeError("allergy must refuse the batch")
    except Exception as exc:
        assert "ALLERGY" in str(exc).upper()
    assert allergic_orders.orders == {}  # nothing drafted at all
    log["mdt_allergy_refused"] = True
    log["mdt_orders_placed"] = len(out["orders"])

    # ---- 05:45 TNM staging: the edition is part of the stage (P1)
    stg = StagingRegistry()
    st = stg.stage("p-colon", site="colon", t="3", n="1a", m="0",
                   basis="pathological", by="path-1", day=2,
                   evidence_ref="SYN-1")
    assert st["notation"] == "pT3N1AM0"
    try:
        stg.stage("p-colon", site="colon", t="3", n="1a", m="0",
                  basis="clinical", by="onc-1", day=2,
                  evidence_ref="ct-1", edition="ajcc-7")
        raise RuntimeError("unshaped edition must refuse")
    except Exception as exc:
        assert "edition" in str(exc)
        log["tnm_edition_refused"] = True
    stg.stage("p-colon", site="colon", t="3", n="1a", m="1b",
              basis="clinical", by="onc-1", day=92,
              evidence_ref="ct-scan-92")
    cur = stg.current_stage("p-colon", site="colon")
    assert cur["restaged"] and cur["history"][0]["m"] == "0"
    log["tnm_restaged"] = cur["current"]["notation"]

    # ---- 06:00 frozen section: its own clock, a documented
    # read-back, and a paraffin correlation that counts (P1)
    fsz = FrozenSectionLog()
    fsz.request("FS-1", patient_id="p-theatre", site="breast lump",
                surgeon="surg-1", facility="phc-1", day=2, minute=600)
    res = fsz.report_result("FS-1", finding="malignant", by="path-1",
                            day=2, minute=624, read_back_by="surg-1")
    assert res["within_target"]
    try:
        fsz.request("FS-2", patient_id="p-theatre2", site="ovary",
                    surgeon="surg-1", facility="phc-1", day=2,
                    minute=700)
        fsz.report_result("FS-2", finding="benign", by="path-1",
                          day=2, minute=710, read_back_by="")
        raise RuntimeError("no read-back must refuse")
    except TeleReadError:
        log["frozen_readback_refused"] = True
    fsz.correlate_permanent("FS-1", permanent_finding="malignant",
                            by="path-1", day=4)
    log["frozen_tat_minutes"] = res["tat_minutes"]

    # ---- 06:15 derivatives: blocks and slides are physical assets
    # with a location trail (P1)
    inv = DerivativeInventory(path)
    inv.register_block("B-COLON-1", specimen_id="SP-COLON",
                       location="paraffin-store-A", day=2, by="lab-1")
    slides = inv.cut_slides("B-COLON-1", n_slides=4, stain="h-and-e",
                            location="lab-bench-2", day=2, by="lab-1")
    inv.move(slides[0], to_location="scanner-1", day=2, by="lab-2")
    assert inv.locate(slides[0])["location"] == "scanner-1"
    assert len(inv.locate(slides[0])["trail"]) == 2
    log["derivative_slides"] = len(slides)

    # ---- 06:30 reflex testing: ordered from the verified report,
    # returning as an addendum — never an orphan result (P1)
    rx = ReflexLog(path.reading)
    rx.order("RX-C1", case_id="PC-COLON", test="ihc-MLH1",
             reason="Lynch syndrome screen", by="path-1", day=2)
    try:
        rx.order("RX-C2", case_id="PC-COLON", test="fish-HER2",
                 reason="r", by="path-1", day=2)
        raise RuntimeError("off-menu test must refuse")
    except TeleReadError:
        log["reflex_off_menu_refused"] = True
    rx.record_result("RX-C1", result="loss of expression",
                     by="path-1", day=5)
    assert path.reading.reports["PC-COLON"]["state"] == "addended"
    log["reflex_addended"] = "PC-COLON"

    # ---- 07:00 Holter: a 24h monitor with 3 usable hours is a
    # repeat, not a negative; AF burden carries its denominator (P1)
    holter = HolterService()
    holter.start_monitor("HM-1", patient_id="p-palp", kind="holter-24h",
                         device_id="ecg-h1", facility="phc-1", day=2)
    holter.end_monitor("HM-1", day=3, usable_hours=22.5)
    holter.annotate_episode("HM-1", kind="patient-symptom", day=3,
                            minute=410, duration_s=30,
                            note="palpitations climbing stairs")
    ana = holter.record_analysis("HM-1", by="card-1", day=3,
                                 total_beats=108400, min_hr=47,
                                 avg_hr=74, max_hr=152,
                                 af_burden_pct=6.3)
    assert any("stroke-risk" in f for f in ana["flags"])
    holter.start_monitor("HM-2", patient_id="p-palp2",
                         kind="holter-24h", device_id="ecg-h1",
                         facility="phc-1", day=2)
    holter.end_monitor("HM-2", day=3, usable_hours=3.0)
    try:
        holter.record_analysis("HM-2", by="card-1", day=3,
                               total_beats=15000, min_hr=50,
                               avg_hr=65, max_hr=90)
        raise RuntimeError("insufficient recording must refuse "
                           "analysis")
    except TeleReadError:
        log["holter_insufficient_refused"] = True
    assert holter.monitor_report()["analyzable_rate"] == {
        "n": 1, "of": 2, "rate": 0.5}

    # ---- 07:15 machine interpretation edit trail: the machine's read
    # is baseline, the human's edit is the trust signal (P2)
    trail = MachineEditTrail(ecg)
    trail.record_machine_interpretation(
        "T-77", text="Normal ECG", codes=["normal"],
        sw_version="carto-v7.2")
    final = trail.record_final_interpretation(
        "T-77", text="Sinus rhythm, first-degree AV block",
        codes=["sinus-rhythm", "first-degree-block"], by="card-1",
        day=2)
    assert final["edited"]
    assert "normal" in final["codes_removed"]
    log["ecg_machine_edited"] = True

    # ---- 07:30 dialysis adequacy variants + vaccination series:
    # named formulas, honest intervals, titres prove response (P1)
    adq = adequacy_from_labs(pre_bun=90, post_bun=27, uf_liters=2.4,
                             post_weight_kg=68, duration_min=240)
    assert adq["spktv_daugirdas2"] >= 1.2
    assert adq["flags"] == []
    short = adequacy_from_labs(pre_bun=90, post_bun=45, uf_liters=1.5,
                               post_weight_kg=68, duration_min=90)
    assert short["flags"], "a 90-minute run must flag"
    book = VaccinationBook(unit)
    book.record_dose("p-ckd", "hbv", dose_number=1, day=0,
                     by="nurse-1")
    book.record_dose("p-ckd", "hbv", dose_number=2, day=30,
                     by="nurse-1")
    book.record_dose("p-ckd", "hbv", dose_number=3, day=180,
                     by="nurse-1")
    titre = book.record_titre("p-ckd", "hbv", titre_miu_ml=4.0,
                              day=240)
    assert not titre["responder"]
    assert "repeat full series" in titre["action"]
    log["hbv_nonresponder_actioned"] = True

    # ---- 07:45 registry returns: the year in numbers, denominators
    # attached (P1); lesion map: a changing mole flags (P1)
    frr = fert_registry_return(clinic)
    assert frr["cycles_started"] >= 1
    log["fert_registry_cycles"] = frr["cycles_started"]
    lm = LesionMap()
    lm.enroll("p-mel", risk_tier="very-high", day=0, by="derm-1")
    lm.map_lesion("p-mel", "L-9", zone="back-lower",
                  image_ref="img://l9-0", day=0, by="derm-1",
                  size_mm=5.0, colours=("brown",))
    chg = lm.add_image("p-mel", "L-9", image_ref="img://l9-90", day=90,
                       by="derm-1", size_mm=7.5,
                       colours=("brown", "black"))
    assert len(chg["flags"]) == 2
    due = lm.surveillance_due(as_of=95)
    assert due[0]["patient_id"] == "p-mel" and due[0]["days_overdue"] == 5
    log["lesion_change_flagged"] = "L-9"

    # ---- 08:00 diagnostic viewer: the pixels are the point (P0);
    # verification gates on the launch (crosslink seam 16)
    pacs_svc.register_study(study_uid="ST-PX1", accession="ACC-PX1",
                            patient_id="p-px", modality="CT",
                            exam_protocol="ct-chest", priority="routine",
                            facility="phc-1", day=0)
    launcher = ViewerLauncher(pacs_svc,
                              viewer_base="https://ohif.example")
    radiology.order("RC-PX1", priority="routine", question="?nodule",
                    facility="phc-1", referrer="mo-1", day=0,
                    scope="chest")
    radiology.assign("RC-PX1", day=0, prefer="rad-on")
    radiology.draft_report("RC-PX1", reader_id="rad-on",
                           findings="6mm nodule", impression="follow-up",
                           template_version="freeform@1", day=0)
    try:
        verify_with_pixels(radiology, launcher, "RC-PX1",
                           artifact="ST-PX1", reader_id="rad-on", day=0)
        raise RuntimeError("verified without opening the pixels")
    except CrosslinkError:
        log["pixel_gate_held"] = True
    launch = launcher.launch_study("ST-PX1", reader_id="rad-on",
                                   purpose="diagnosis", day=0,
                                   case_id="RC-PX1")
    verified = verify_with_pixels(radiology, launcher, "RC-PX1",
                                  artifact="ST-PX1", reader_id="rad-on",
                                  day=0)
    assert verified["state"] == "verified"
    assert verified["viewer_session"] == launch["session_id"]
    log["pixel_gate_verified"] = verified["viewer_session"]

    # ---- 08:10 tele-ICU family log: silence to the family is a
    # finding (P2)
    famlog = FamilyLog(icu)
    open_icu = sorted(icu.oversight)[0]
    famlog.record_update(open_icu, day=0, by="icu-1",
                         family_member="spouse", channel="phone",
                         summary="stable on reduced sedation")
    assert famlog.updates_due(0) == []
    log["family_update_logged"] = open_icu

    # ---- 08:20 IV Y-site + TPN: the tubing is a prescribing surface
    # too (P2)
    assert check_iv_y_site("ceftriaxone", "calcium-gluconate"
                           )["verdict"] == "incompatible"
    assert check_iv_y_site("obscuremycin", "rarezepam"
                           )["verdict"] == "unrated"
    tpn = tpn_assess(volume_ml=2000, dextrose_pct=10, amino_acid_pct=4,
                     lipid_pct=2, calcium_mmol=5, phosphate_mmol=15,
                     route="central")
    assert tpn["route_verdict"] == "route-ok"
    hot = tpn_assess(volume_ml=2000, dextrose_pct=15, amino_acid_pct=5,
                     lipid_pct=2, calcium_mmol=5, phosphate_mmol=15,
                     route="peripheral")
    assert hot["route_verdict"] == "refused-peripheral"
    log["iv_tpn_screened"] = True

    # ---- 08:25 machine fault → chair re-seat: the fault interrupts
    # loudly, the re-seat re-checks segregation (P2)
    unit.register_machine("HD-M1")
    unit.register_machine("HD-M2")
    unit.start_session("HD-F1", patient_id="p-ckd", machine_id="HD-M1",
                       dialyzer_id="DZ-1", day=1)
    fault = unit.record_machine_fault("HD-M1", day=1,
                                      description="blood leak alarm",
                                      by="nurse-1")
    assert fault["sessions_interrupted"] == ["HD-F1"]
    moved = unit.reseat_session("HD-F1", new_machine_id="HD-M2", day=1,
                                by="nurse-1")
    assert moved["state"] == "running"
    log["fault_reseated"] = moved["machine_id"]

    # ---- 08:30 PGT gate: the biopsy is a question; the result gates
    # the transfer (P2)
    clinic.register_couple("CPL-PGT", partner_a="ife", partner_b="seun",
                           day=0)
    for kind in ("cycle-treatment", "embryo-transfer", "pgt"):
        for who in ("ife", "seun"):
            clinic.grant_consent("CPL-PGT", kind, method="signed",
                                 by=who, day=0)
    clinic.start_cycle("CY-PGT", couple_id="CPL-PGT",
                       protocol_version="antag@2", day=0)
    for stage in ("stimulation", "triggered", "retrieval",
                  "fertilization"):
        clinic.advance("CY-PGT", stage, day=1, by="dr-1")
    clinic.fertilize("CY-PGT", method="icsi", n_embryos=2, day=2,
                     embryologist="em-1")
    clinic.advance("CY-PGT", "culture", day=3, by="em-1")
    clinic.order_pgt("CY-PGT-E1", kind="pgt-a", day=4, by="em-1")
    try:
        clinic.move_embryo("CY-PGT-E1", "transferred", day=5,
                           witness_1="em-1", witness_2="em-2")
        raise RuntimeError("transferred with an unanswered biopsy")
    except FertilityError:
        log["pgt_no_result_held"] = True
    clinic.record_pgt_result("CY-PGT-E1", result="aneuploid", day=5,
                             lab_ref="GEN-1")
    try:
        clinic.move_embryo("CY-PGT-E1", "transferred", day=6,
                           witness_1="em-1", witness_2="em-2")
        raise RuntimeError("transferred an aneuploid embryo")
    except FertilityError:
        log["pgt_aneuploid_held"] = True

    # ---- 08:35 growth charting: the series is the chart (P2)
    ga = growth_assessment([(26, {"ac": 220}), (30, {"ac": 262}),
                            (34, {"ac": 272})])
    assert any("crossed centile bands" in f for f in ga["flags"])
    log["growth_crossing_flagged"] = True

    # ---- 08:40 device hub: machines meet the platform (P1) — open
    # lane parses clean-room HL7, composed lanes refuse without the
    # vendor transport
    hub = DeviceHub()
    lis = hub.connect("open-lis", endpoint="tcp://lis.phc-1:2575")
    feed = hub.ingest(
        lis["session_id"],
        "MSH|^~\\&|COBAS6000|ROCHE|OPENHEALTH|LAB|20261103083000||"
        "ORU^R01|MSG-1|P|2.5\r"
        "PID|1||p-ckd\r"
        "OBX|1|NM|K^Potassium||5.2|mmol/L|3.5-5.1|H||||||"
        "20261103082500\r")
    assert feed["observations"][0]["code"] == "K"
    assert feed["observations"][0]["abnormal_flag"] == "H"
    assert feed["observations"][0]["source_adapter"] == "open-lis"
    try:
        hub.connect("ge-muse", endpoint="https://muse.hospital")
        raise RuntimeError("composed lane without transport connected")
    except DeviceHubError:
        log["vendor_sdk_never_vendored"] = True
    out_msg = hub.push_result(lis["session_id"], message_id="HD-9",
                              patient_id="p-ckd",
                              observations=[{"code": "SPKTV",
                                             "value": "1.45"}],
                              panel="HD-ADEQUACY")
    assert "SPKTV" in out_msg
    log["device_feed_ingested"] = feed["message_id"]

    # ---- 08:42 device link layer (P0–P3): the wire, the spool, the
    # lifecycle, the identity, the units — seamless means nothing
    # lost, nothing guessed, nothing doubled
    import datetime as _dtl
    import mllp
    import astm
    from devicelink import FeedHealth, ResultStore, Spool
    from patientlink import IdentityMap
    from units import normalize_observation
    # P0a: MLLP — a real socket round trip, honest ACK
    received = []

    def _lis_dispatch(message):
        parsed = parse_oru_hl7(message)
        received.append(parsed)
        from devicehub import build_ack as _ack
        return _ack(parsed["message_id"])
    listener = mllp.MLLPListener("127.0.0.1", 0, _lis_dispatch)
    listener.serve_in_thread()
    mllp_port = listener.server_address[1]
    ack = mllp.send("127.0.0.1", mllp_port,
                    "MSH|^~\\&|COBAS|ROCHE|OH|LAB|2026||ORU^R01|MSG-MLLP|"
                    "P|2.5\rPID|1||p-ckd\r"
                    "OBX|1|NM|GLU^Glucose||180|mg/dL||H|||F|||"
                    "20261103084200\r")
    assert "MSA|AA|MSG-MLLP" in ack
    listener.shutdown()
    listener.server_close()
    log["mllp_wire_proven"] = True
    # P0b: spool — link down loses nothing, replay delivers
    spool = Spool()
    spool.enqueue(lis["session_id"], "outbound", {"msg": "HD-9"})
    dead = spool.replay(lis["session_id"],
                        lambda p: (_ for _ in ()).throw(
                            ConnectionError("link down")))
    assert dead["failed"] == 1
    delivered = []
    spool.replay(lis["session_id"], delivered.append)
    assert delivered == [{"msg": "HD-9"}]
    log["spool_replay_proven"] = True
    # P1a: dedupe + lifecycle — retry safe, correction supersedes
    store = ResultStore()
    v1 = parse_oru_hl7(
        "MSH|^~\\&|A|B|C|D|2026||ORU^R01|MSG-K1|P|2.5\rPID|1||p-ckd\r"
        "OBX|1|NM|K^Potassium||5.2|mmol/L||N|||F|||20261103082500\r")
    store.post(v1)
    dup = store.post(v1)
    assert dup["verdict"] == "duplicate"
    v2 = parse_oru_hl7(
        "MSH|^~\\&|A|B|C|D|2026||ORU^R01|MSG-K2|P|2.5\rPID|1||p-ckd\r"
        "OBX|1|NM|K^Potassium||4.9|mmol/L||N|||C|||20261103083000\r")
    assert store.post(v2)["observations"] == ["superseded"]
    assert store.latest("p-ckd", "K")["value"] == "4.9"
    assert len(store.history("p-ckd", "K")) == 2
    log["result_lifecycle_proven"] = True
    # P1b: feed health — silence surfaces, garbage quarantines
    fh = FeedHealth()
    fh.seen(lis["session_id"], _dtl.datetime(2026, 1, 1, 8, 0))
    fh.set_expectation(lis["session_id"], 300)
    rep = fh.report(_dtl.datetime(2026, 1, 1, 9, 0))
    assert rep["stale"] == [lis["session_id"]]
    log["feed_silence_is_a_finding"] = True
    # P2a: identity crosswalk — unknown patient quarantines, merge
    # follows the chain
    im = IdentityMap()
    q = im.route_result(v1)
    assert q["quarantined"] is True
    im.register("p-ckd", "patient-ckd-1", by="registrar")
    r = im.route_result(v1)
    assert r["platform_patient_id"] == "patient-ckd-1"
    log["device_identity_crosswalk_proven"] = True
    # P2b: units — mg/dL becomes mmol/L with provenance, unbound is
    # stated
    conv = normalize_observation(
        {"code": "GLU", "value": "180", "value_numeric": 180.0,
         "units": "mg/dL"}, target_unit="mmol/L")
    assert conv["loinc"] == "2345-7"
    assert 9.9 < conv["value_numeric"] < 10.1
    log["unit_conversion_proven"] = True
    # P3: ASTM lane — the old analyzer's checksum is the contract
    session = (f"{astm.ENQ}"
               f"{astm.build_frame(1, 'P|1||p-ckd')}"
               f"{astm.build_frame(2, 'R|1|^^^GLU|95|mg/dL||N||||F')}"
               f"{astm.EOT}")
    parsed_astm = astm.parse_session(session)
    assert parsed_astm["observations"][0]["code"] == "GLU"
    log["astm_lane_proven"] = True

    # ---- 08:43 the production layer: durability, TLS posture, order
    # download, and the feed-health escalation seam (seam 19)
    import io as _io
    import tempfile as _tmp
    from orderdownload import build_orm, parse_orm
    from persist import DurableResultStore, DurableSpool, Journal
    from seriallane import read_session_from
    from crosslink import feed_health_to_oversight
    # durability: the spool survives a process restart
    jdir = _tmp.mkdtemp()
    spool_a = DurableSpool(Journal(f"{jdir}/spool.jsonl"))
    spool_a.enqueue(lis["session_id"], "outbound", {"msg": "HD-10"})
    spool_b = DurableSpool(Journal(f"{jdir}/spool.jsonl"))  # "restart"
    assert len(spool_b.pending()) == 1
    log["spool_survives_restart"] = True
    # TLS posture: plaintext to a routable address refuses
    plaintext_refused = False
    try:
        mllp.check_tls_posture("192.168.4.20", None)
    except mllp.MLLPError:
        plaintext_refused = True
    assert plaintext_refused
    log["plaintext_mllp_refused"] = True
    # order download: STAT order to the bench, round-tripped
    orm = build_orm(message_id="ORD-1", placer_order_number="PO-77",
                    patient_id="p-ckd", test_code="CHEM7", priority="S",
                    ordering_provider="dr-1")
    assert parse_orm(orm)["priority"] == "S"
    log["order_download_proven"] = True
    # serial lane: session from a byte stream, noise counted
    srec = read_session_from(_io.BytesIO(session.encode("latin-1")))
    assert srec["observations"][0]["value"] == "95"
    log["serial_lane_proven"] = True
    # seam 19: the stale feed pages a human, once
    icu19 = TeleICU(readers)
    opened19 = {}
    esc = feed_health_to_oversight(fh, icu19, facility="phc-1", day=1,
                                   opened=opened19)
    assert len(esc["opened"]) == 1
    assert feed_health_to_oversight(fh, icu19, facility="phc-1", day=1,
                                    opened=opened19)["opened"] == []
    log["feed_oversight_seam"] = esc["opened"][0]

    # ---- 08:45 traveler medical pass: the border collects the fee,
    # the QR opens the record, the emergency never waits for the
    # invoice (new domain)
    tpr = TravelPassRegistry("ng")
    tpr.enroll("TV-1", full_name="Jane Doe", passport_ref="DE-A1234",
               nationality="DE", entry_day=0, exit_day=13,
               emergency_profile={"blood_group": "O+",
                                  "allergies": ["penicillin"],
                                  "conditions": ["asthma"],
                                  "medications": ["salbutamol"],
                                  "emergency_contacts": [
                                      {"name": "Ada",
                                       "phone": "+234…"}]},
               day=0)
    premium = tpr.travelers["TV-1"]["assessed_premium_kobo"]
    # a partial payment is a stated shortfall, not a silent discount
    tpr.pay("TV-1", amount_kobo=premium - 1000, method="card",
            reference="psp-1", day=0)
    assert tpr.coverage_status("TV-1", 2)["status"] == "shortfall"
    tpr.pay("TV-1", amount_kobo=1000, method="transfer",
            reference="psp-2", day=0)
    assert tpr.coverage_status("TV-1", 2)["status"] == "active"
    issued = tpr.issue_pass("TV-1", day=0)
    assert "TV-1" not in issued["token"]  # the QR is a key
    # break-glass without a reason is a breach, not access
    try:
        tpr.emergency_access(issued["token"], provider_id="para-1",
                             facility="er-lagos", reason="", day=2)
        raise RuntimeError("break-glass without attribution opened")
    except TravelPassError:
        log["travelpass_breach_refused"] = True
    card = tpr.emergency_access(issued["token"], provider_id="para-1",
                                facility="er-lagos",
                                reason="rta-unresponsive", day=2)
    assert card["blood_group"] == "O+"
    assert "passport_ref" not in card  # emergency subset only
    ev = tpr.record_emergency("TV-1", session_id=card["session_id"],
                              day=2, facility="er-lagos",
                              summary="anaphylaxis, treated")
    assert ev["payable"] is True
    log["travelpass_emergency_paid"] = ev["payable"]
    # the enrollment reaches the EMR: IPS bundle any conformant EMR
    # imports, and the break-glass encounter links through a sink
    bundle = ips_document(tpr, "TV-1", author="travelpass", day=2)
    assert bundle["resourceType"] == "Bundle"
    enc = emr_encounter(tpr, "TV-1", card["session_id"], day=2,
                        sink=lambda ref, e: "EMR-ENC-1")
    assert enc["emr_link"] == "linked"
    log["travelpass_emr_linked"] = enc["emr_ref"]
    # channels: NDJSON chat-seam lines carry pointers, never PHI
    line = notification_line(tpr, "emergency-accessed", "TV-1", day=2,
                             content_ref=f"er://{card['session_id']}")
    assert "anaphylaxis" not in line and "penicillin" not in line
    log["travelpass_notified"] = True
    # the money: admin/pool split settles over payrails; refunds are
    # pro-rata pool share of unused days, never after an emergency
    entry = settle(tpr, RailRouter(), day=2, pool_account="POOL-NG",
                   admin_account="ADMIN-NG")
    assert entry["admin_kobo"] + entry["pool_kobo"] == \
        entry["gross_kobo"]
    log["travelpass_settled"] = entry["settlement_id"]
    try:
        refund_early_exit(tpr, "TV-1", actual_exit_day=6, day=6,
                          reason="left early")
        raise RuntimeError("refunded after an emergency event")
    except TravelPassError:
        log["travelpass_refund_blocked"] = True

    # ---- 08:50 the traveler pays with the card they carry — Visa
    # through a composed PSP (Stripe-shaped); the PAN never lands, only
    # scheme/last4/hash reach the ledger (seam-adjacent: cards)
    import travelpass_cards as cards
    tpr.enroll("TV-2", full_name="Ken Watanabe", passport_ref="JP-9982",
               nationality="JP", entry_day=0, exit_day=13,
               emergency_profile={"conditions": ["measles"]}, day=0)
    premium2 = tpr.travelers["TV-2"]["assessed_premium_kobo"]
    cnp_refused = False
    try:
        cards.pay_card(tpr, "TV-2", pan="4242424242424242",
                       expiry="12/29", holder="Ken Watanabe",
                       psp_name="stripe", psp_charge=lambda p:
                       {"status": "approved", "auth_code": "A1"},
                       amount_kobo=premium2, reference="psp-tv2",
                       day=0, authenticated=False)
    except TravelPassError:
        cnp_refused = True
    assert cnp_refused
    log["card_cnp_without_3ds_refused"] = True
    paid = cards.pay_card(tpr, "TV-2", pan="4242424242424242",
                          expiry="12/29", holder="Ken Watanabe",
                          psp_name="stripe", psp_charge=lambda p:
                          {"status": "approved", "auth_code": "A1"},
                          amount_kobo=premium2, reference="psp-tv2",
                          day=0, authenticated=True)
    assert paid["card"]["scheme"] == "visa"
    assert "4242424242424242" not in str(tpr.payments)
    log["card_paid_scheme"] = paid["card"]["scheme"]

    # ---- 08:55 vaccination card: the yellow-fever stamp has a batch,
    # and entry clearance checks the onset window (day 10) not vibes
    no_batch = False
    try:
        cards.record_vaccination(tpr, "TV-2", vaccine="yellow-fever",
                                 day=0, certificate_ref="ICVP-77",
                                 batch="", administering_country="jp")
    except TravelPassError:
        no_batch = True
    assert no_batch
    log["vaccine_stamp_without_batch_refused"] = True
    cards.record_vaccination(tpr, "TV-2", vaccine="yellow-fever", day=0,
                             certificate_ref="ICVP-77",
                             batch="YFV-2026-01",
                             administering_country="jp")
    too_fresh = cards.entry_clearance(tpr, "TV-2", destination="ng",
                                      day=5)
    assert too_fresh["cleared"] is False
    assert "onset window" in too_fresh["gaps"][0]
    cleared = cards.entry_clearance(tpr, "TV-2", destination="ng",
                                    day=12)
    assert cleared["cleared"] is True
    log["vaccine_clearance_onset_enforced"] = True

    # ---- 09:00 seams 17+18: the border is the first epidemiological
    # sensor. The PoE screen stamps the pass; the notifiable emergency
    # feeds the syndrome ring with (syndrome, area, day), never the
    # traveler
    import datetime as _dt
    from surveillance import Surveillance
    from poe_screen import AlertList, BorderPost
    alerts_list = AlertList()
    alerts_list.publish("who-2026-w01", ["freedonia"], "2026-01-01")
    post = BorderPost("MMA-LOS", alerts_list)
    poe = poe_entry_screen(tpr, post, "TV-2",
                           list_version="who-2026-w01",
                           travel_history_21d=["freedonia"],
                           temp_c=38.6, symptoms=["cough"],
                           screener="officer-1", day="2026-01-02")
    assert poe["disposition"] == "isolate-and-notify"
    assert "public_health_hold" in tpr.travelers["TV-2"]
    log["poe_hold_on_pass"] = True
    surv = Surveillance()
    issued2 = tpr.issue_pass("TV-2", day=0)
    acc2 = tpr.emergency_access(issued2["token"], provider_id="para-2",
                                facility="er-ikeja",
                                reason="febrile rash", day=2)
    tpr.record_emergency("TV-2", session_id=acc2["session_id"], day=2,
                         facility="er-ikeja",
                         summary="measles suspected, isolated")
    sig = imported_case_signal(tpr, surv, traveler_id="TV-2", day=2,
                               base_date=_dt.date(2026, 1, 1))
    assert sig["imported_signals"] == 1
    assert sig["events"][0]["syndrome"] == "measles_like_rash"
    assert "Ken" not in str(sig["events"])  # the ring never sees a name
    log["imported_signal_to_surveillance"] = sig["imported_signals"]
    cleared_hold = clear_public_health_hold(
        tpr, "TV-2", day=9, by="dr-1", clearance_ref="PCR-NEG-1")
    assert "public_health_hold" not in tpr.travelers["TV-2"]
    log["poe_hold_cleared_with_ref"] = cleared_hold["clearance_ref"]

    log["seams_proven"] = 34
    return log


if __name__ == "__main__":
    import json
    print(json.dumps(run_connected_day(), indent=1))
