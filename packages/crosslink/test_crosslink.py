"""test_crosslink.py — every seam tested at both ends: the happy
composition, and the fail-closed refusal when the other side is absent,
stale, or unsafe."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("crosslink", "nephrology", "telederm", "telepath", "teleread",
            "reportkit", "pacs", "fertility", "rota", "triggers", "vitals",
            "devicereg", "teleicu", "mdt", "duelgrade", "drugkb",
            "steward", "pharmreview", "earlywarn", "serial",
            "cervixscreen", "callrecall", "inpatient", "medrec",
            "cpoe", "viewerlaunch"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from crosslink import (CrosslinkError, RENAL_RULES_VERSION, renal_check,  # noqa: E402
                       biopsy_to_path, case_pack, submit_with_pack,
                       cryo_watch, chair_plan, on_shift_readers,
                       assign_on_shift, escalate_events,
                       timeouts_to_review, deterioration_to_icu,
                       order_with_priors, screens_to_register,
                       colpo_biopsy_to_path, admissions_to_medrec,
                       synoptic_signout, decision_to_orders,
                       verify_with_pixels)
from viewerlaunch import ViewerLauncher  # noqa: E402
from nephrology import DialysisUnit  # noqa: E402
from telederm import TeleDerm, DERM_CHECKS  # noqa: E402
from telepath import PathologyService  # noqa: E402
from teleread import (ReaderRegistry, TeleReadError, DomainConfig,  # noqa: E402
                      ReadingService)
from reportkit import ReportKit  # noqa: E402
from pacs import StudyRegistry  # noqa: E402
from fertility import FertilityClinic  # noqa: E402
from rota import Rota, Shift, Staff  # noqa: E402
from triggers import Rule, TriggerEngine  # noqa: E402
from vitals import VitalsIngest  # noqa: E402
from teleicu import TeleICU  # noqa: E402
from mdt import TumourBoard  # noqa: E402
from cervixscreen import CervixScreening  # noqa: E402
from callrecall import CallRecallRegister  # noqa: E402
from inpatient import Ward  # noqa: E402
from medrec import MedRec  # noqa: E402


class TestRenalCheck(unittest.TestCase):
    def setUp(self):
        self.unit = DialysisUnit("renal-1")
        self.unit.register_patient("p1", dry_weight_kg=70, egfr=25, day=0)
        self.unit.register_patient("p2", dry_weight_kg=80, egfr=40, day=0)
        self.unit.register_patient("p3", dry_weight_kg=65, egfr=90, day=0)

    def test_contraindicated_band_wins(self):
        r = renal_check(self.unit, "p1", "metformin", day=1)
        self.assertEqual(r["verdict"], "contraindicated")
        self.assertEqual(r["ckd_stage"], "G4")
        self.assertEqual(r["rule_version"], RENAL_RULES_VERSION)

    def test_dose_adjust_band(self):
        r = renal_check(self.unit, "p2", "metformin", day=1)
        self.assertEqual(r["verdict"], "dose-adjust")

    def test_class_resolution_via_formulary(self):
        # ibuprofen has no drug-level row; the nsaid class row applies
        r = renal_check(self.unit, "p1", "ibuprofen", day=1)
        self.assertEqual(r["verdict"], "contraindicated")
        self.assertIn("nsaid", r["matched"])

    def test_clear_above_all_bands(self):
        self.assertEqual(renal_check(self.unit, "p3", "metformin",
                                     day=1)["verdict"], "clear")

    def test_unknown_patient_refuses(self):
        with self.assertRaises(CrosslinkError):
            renal_check(self.unit, "ghost", "metformin", day=1)

    def test_unrated_drug_is_not_clearance(self):
        r = renal_check(self.unit, "p1", "paracetamol", day=1)
        self.assertEqual(r["verdict"], "unrated")
        self.assertIn("not clearance", r["note"])

    def test_stale_egfr_flagged(self):
        self.unit.record_egfr("p3", 28, day=0)
        r = renal_check(self.unit, "p3", "metformin", day=200)
        self.assertEqual(r["verdict"], "contraindicated")
        self.assertTrue(any("egfr-stale" in f for f in r["flags"]))


class TestBiopsyToPath(unittest.TestCase):
    def setUp(self):
        self.readers = ReaderRegistry()
        self.readers.register("derm-1", licence_id="MDCN-D1",
                              licence_expiry_day=400,
                              scopes=("dermatologist",))
        self.readers.register("path-1", licence_id="MDCN-P1",
                              licence_expiry_day=400,
                              scopes=("histopathologist",))
        self.derm = TeleDerm(self.readers)
        self.path = PathologyService(self.readers)
        self.derm.assess_capture("img://1", day=0, by="nurse-1",
                                 passed=DERM_CHECKS)
        self.derm.submit_lesion("DC1", patient_id="p1",
                                body_site="left forearm",
                                image_ref="img://1", consent="signed",
                                facility="phc-1", referrer="mo-1", day=0,
                                duration_days=30)

    def test_biopsy_registers_sited_specimen(self):
        out = biopsy_to_path(self.derm, self.path, "DC1", "SP1",
                             day=1, collector="nurse-1", facility="phc-1")
        s = out["specimen"]
        self.assertEqual(s["site"], "left forearm")
        self.assertEqual(s["patient_id"], "p1")
        self.assertEqual(s["state"], "collected")

    def test_unknown_derm_case_refuses(self):
        with self.assertRaises(CrosslinkError):
            biopsy_to_path(self.derm, self.path, "ghost", "SP2",
                           day=1, collector="nurse-1", facility="phc-1")

    def test_seam_does_not_skip_custody(self):
        biopsy_to_path(self.derm, self.path, "DC1", "SP1",
                       day=1, collector="nurse-1", facility="phc-1")
        with self.assertRaises(TeleReadError):  # collected ≠ scanned
            self.path.submit_for_reading(
                "SP1", case_id="PC1", priority="routine",
                question="?bcc", referrer="derm-1", day=1,
                artifact="wsi-scan")


class TestCasePack(unittest.TestCase):
    def setUp(self):
        self.rk = ReportKit(signing_key=b"test-key")
        self.rk.draft("R1", template_id="ecg-12lead@1.0.0",
                      answers={"rate_bpm": 80, "rhythm": "sinus",
                               "pr_ms": 160, "qrs_ms": 90, "qtc_ms": 400,
                               "findings": "normal",
                               "interpretation": "nsr",
                               "acuity": "routine"},
                      case_id="C1", author_id="rad-1", day=1)
        self.rk.draft("R2", template_id="ecg-12lead@1.0.0",
                      answers={"rate_bpm": 81, "rhythm": "sinus",
                               "pr_ms": 161, "qrs_ms": 93, "qtc_ms": 401,
                               "findings": "normal",
                               "interpretation": "nsr",
                               "acuity": "routine"},
                      case_id="C1", author_id="rad-1", day=1)
        self.rk.sign("R1", signer_id="rad-1", day=2)  # R2 stays a draft

        self.readers = ReaderRegistry()
        self.path = PathologyService(self.readers)
        self.path.register_specimen("SP1", patient_id="p1",
                                    specimen_type="biopsy", site="colon",
                                    collector="n1", facility="f1", day=0)
        for st in ("fixed", "processed", "sectioned", "stained",
                   "scanned"):
            kw = {"fixation_hours": 12} if st == "processed" else {}
            self.path.advance("SP1", st, day=1, by="lab-1", **kw)
        self.path.register_specimen("SP2", patient_id="p1",
                                    specimen_type="biopsy", site="colon",
                                    collector="n1", facility="f1", day=0)

        self.pacs = StudyRegistry()
        self.pacs.register_study(study_uid="1.2.3", accession="A1", patient_id="p1",
                                 modality="CT", exam_protocol="ct-chest",
                                 priority="routine", facility="f1", day=1,
                                 instances=10)

        self.board = TumourBoard("colon-board")
        self.board.add_member("chair-1", role="chair",
                              specialty="oncology")

    def test_pack_pulls_only_final_evidence(self):
        pack = case_pack("p1", case_ids=("C1",), rk=self.rk,
                         path=self.path, pacs_svc=self.pacs)
        self.assertTrue(any(r.startswith("report:R1#")
                            for r in pack["evidence_refs"]))
        self.assertIn("specimen:SP1@scanned", pack["evidence_refs"])
        self.assertIn("study:1.2.3", pack["evidence_refs"])
        # the draft and the mid-custody specimen are findings, not evidence
        self.assertTrue(any("R2" in f and "draft" in f
                            for f in pack["findings"]))
        self.assertTrue(any("SP2" in f for f in pack["findings"]))

    def test_submit_with_pack(self):
        pack = case_pack("p1", case_ids=("C1",), rk=self.rk,
                         path=self.path, pacs_svc=self.pacs)
        out = submit_with_pack(self.board, "MDT1", patient_id="p1",
                               question="resect or surveil?", pack=pack,
                               submitted_by="chair-1", day=3)
        self.assertEqual(out["state"], "awaiting-meeting")
        self.assertEqual(len(out["evidence_refs"]), 3)

    def test_empty_pack_refuses_loudly(self):
        pack = case_pack("ghost-patient", case_ids=(), rk=self.rk,
                         path=self.path, pacs_svc=self.pacs)
        with self.assertRaises(CrosslinkError):
            submit_with_pack(self.board, "MDT2", patient_id="ghost",
                             question="?", pack=pack,
                             submitted_by="chair-1", day=3)


class TestCryoWatch(unittest.TestCase):
    def setUp(self):
        self.cl = FertilityClinic("fert-1")
        self.cl.register_couple("CPL", partner_a="pa", partner_b="pb",
                                day=0)
        for p in ("pa", "pb"):
            for kind in ("cycle-treatment", "cryopreservation"):
                self.cl.grant_consent("CPL", kind, method="signed",
                                      by=p, day=0)
        self.cl.start_cycle("CY1", couple_id="CPL",
                            protocol_version="antag-v3", day=1)
        for stage in ("stimulation", "triggered", "retrieval",
                      "fertilization"):
            self.cl.advance("CY1", stage, day=2, by="dr-1")
        self.cl.fertilize("CY1", method="ivf", n_embryos=1, day=3,
                          embryologist="emb-1")
        self.cl.move_embryo("CY1-E1", "frozen", day=4,
                            witness_1="emb-1", witness_2="emb-2",
                            tank="T1", canister="C2", position="P3")

    @staticmethod
    def _readings(temps, start_h=0):
        return [{"ts": f"2026-01-01T{h:02d}:00:00Z", "celsius": c}
                for h, c in enumerate(temps, start_h)]

    def test_excursion_names_the_embryos(self):
        out = cryo_watch(self.cl, "T1", self._readings(
            [-180, -178, -175, -140]))  # -140 > -150: excursion
        t = out["ticket"]
        self.assertEqual(t["priority"], "P1")
        self.assertEqual([e["embryo_id"] for e in t["embryos_at_risk"]],
                         ["CY1-E1"])
        self.assertEqual(t["embryos_at_risk"][0]["couple_id"], "CPL")

    def test_drift_predicts_breach(self):
        out = cryo_watch(self.cl, "T1", self._readings(
            [-190, -185, -180, -175]))  # +5°C/h → crosses -150 in 5h
        self.assertEqual(out["ticket"]["priority"], "P2")
        self.assertIn("DRIFT", out["ticket"]["reason"])

    def test_stable_tank_no_ticket(self):
        out = cryo_watch(self.cl, "T1", self._readings(
            [-185, -184, -186, -185]))
        self.assertIsNone(out["ticket"])

    def test_abstains_on_thin_data(self):
        out = cryo_watch(self.cl, "T1", self._readings([-180, -140]))
        self.assertIsNone(out["ticket"])
        self.assertIn("abstained", out)

    def test_impossible_reading_refused(self):
        with self.assertRaises(CrosslinkError):
            cryo_watch(self.cl, "T1", self._readings(
                [-180, -181, -179, -250]))  # colder than LN2: broken sensor


class TestChairPlan(unittest.TestCase):
    def setUp(self):
        self.unit = DialysisUnit("renal-1")
        self.unit.register_machine("M1")
        self.unit.register_machine("M2")
        self.unit.register_machine("M-HBV", hbv_dedicated=True)
        self.unit.register_patient("hbv-pos", dry_weight_kg=70, egfr=10,
                                   day=0, sessions_per_week=3)
        self.unit.record_serology("hbv-pos",
                                  results={"hbv": "positive"}, day=0)
        self.unit.register_patient("p-a", dry_weight_kg=70, egfr=12,
                                   day=0, sessions_per_week=3)
        self.unit.register_patient("p-b", dry_weight_kg=70, egfr=12,
                                   day=0, sessions_per_week=3)

    def test_hbv_patient_gets_dedicated_machine(self):
        plan = chair_plan(self.unit, day=1, shifts=("morning",))
        placed = {a["patient_id"]: a for a in plan["shifts"]["morning"]}
        self.assertEqual(placed["hbv-pos"]["machine_id"], "M-HBV")
        self.assertIn(placed["p-a"]["machine_id"], ("M1", "M2"))
        self.assertEqual(plan["waitlist"], [])

    def test_overcapacity_waitlist_is_loud(self):
        self.unit.register_patient("p-c", dry_weight_kg=70, egfr=12,
                                   day=0, sessions_per_week=3)
        plan = chair_plan(self.unit, day=1, shifts=("morning",))
        # 4 patients, 3 chairs in one shift → somebody waits, loudly
        self.assertEqual(len(plan["waitlist"]), 1)

    def test_no_machines_refuses(self):
        empty = DialysisUnit("renal-2")
        empty.register_patient("p", dry_weight_kg=70, egfr=10, day=0)
        with self.assertRaises(CrosslinkError):
            chair_plan(empty, day=1)

    def test_second_shift_absorbs_overflow(self):
        self.unit.register_patient("p-c", dry_weight_kg=70, egfr=12,
                                   day=0, sessions_per_week=3)
        plan = chair_plan(self.unit, day=1, shifts=("morning",
                                                    "afternoon"))
        self.assertEqual(plan["waitlist"], [])
        total = sum(len(v) for v in plan["shifts"].values())
        self.assertEqual(total, 4)


class TestRotaToReading(unittest.TestCase):
    def test_on_shift_pool_governs_assignment(self):
        from teleread import DomainConfig, ReadingService
        readers = ReaderRegistry()
        readers.register("rad-on", licence_id="L1",
                         licence_expiry_day=400, scopes=("chest",))
        readers.register("rad-off", licence_id="L2",
                         licence_expiry_day=400, scopes=("chest",))
        svc = ReadingService(DomainConfig("radiology", scopes=("chest",),
                                          sla_hours={"stat": 2,
                                                     "routine": 24}),
                             readers)
        svc.order("C1", priority="routine", question="?px",
                  facility="f1", referrer="mo", day=0, scope="chest")

        rota = Rota()
        rota.add_staff(Staff("rad-on", "On Duty", "radiologist"))
        rota.add_staff(Staff("rad-off", "Off Duty", "radiologist"))
        rota.add_shift(Shift("D1", "2026-11-02", "day",
                             {"radiologist": 1}))
        rota.assign("D1", "rad-on")

        pool = on_shift_readers(rota, day="2026-05-04")
        self.assertEqual(pool["candidates"], [])  # wrong day, nobody
        pool = on_shift_readers(rota, day="2026-11-02")
        self.assertEqual(pool["candidates"], ["rad-on"])

        out = assign_on_shift(svc, "C1", day=0, pool=pool)
        self.assertEqual(out["assigned_to"], "rad-on")

    def test_off_shift_credentialed_reader_is_not_chosen(self):
        from teleread import DomainConfig, ReadingService
        readers = ReaderRegistry()
        readers.register("rad-off", licence_id="L2",
                         licence_expiry_day=400, scopes=("chest",))
        svc = ReadingService(DomainConfig("radiology", scopes=("chest",),
                                          sla_hours={"stat": 2,
                                                     "routine": 24}),
                             readers)
        svc.order("C1", priority="routine", question="?px",
                  facility="f1", referrer="mo", day=0, scope="chest")
        rota = Rota()  # nobody rostered
        pool = on_shift_readers(rota, day="2026-11-02")
        # teleread's own assign([]) would re-widen to every reader;
        # the seam refuses instead — nobody-on-shift is not a preference
        with self.assertRaises(CrosslinkError):
            assign_on_shift(svc, "C1", day=0, pool=pool)


class TestTriggerToTeleICU(unittest.TestCase):
    def setUp(self):
        self.vitals = VitalsIngest()
        self.engine = TriggerEngine(self.vitals)
        self.engine.add_rule(Rule(
            "spo2-low", version=1, vital="spo2", kind="threshold",
            severity="critical", below=92, condition="pneumonia",
            action_hint="assess for oxygen"))
        self.readers = ReaderRegistry()
        self.readers.register("icu-1", licence_id="MDCN-I1",
                              licence_expiry_day=400,
                              scopes=("intensivist",))
        self.icu = TeleICU(self.readers)

    def _fire(self):
        self.vitals.record("p1", "spo2", 88, day=0,
                           ts="2026-01-01T08:00:00Z")
        fired = self.engine.evaluate("p1", "spo2", day=0)
        return fired[0] if fired else None

    def test_unacknowledged_critical_opens_oversight(self):
        ev = self._fire()
        self.assertIsNotNone(ev)
        opened = {}
        out = escalate_events(self.engine, self.icu, facility="phc-1",
                              day=1, opened=opened)  # day 1: past window
        self.assertEqual(out["opened"], ["icu-EVT-1"])
        self.assertEqual(opened["EVT-1"], "icu-EVT-1")
        ov = self.icu.oversight["icu-EVT-1"]
        self.assertEqual(ov["trigger_ref"], "EVT-1")
        self.assertEqual(ov["patient_id"], "p1")

    def test_idempotent_second_bell_not_second_case(self):
        self._fire()
        opened = {}
        escalate_events(self.engine, self.icu, facility="phc-1",
                        day=1, opened=opened)
        out = escalate_events(self.engine, self.icu, facility="phc-1",
                              day=2, opened=opened)
        self.assertEqual(out["opened"], [])
        self.assertEqual(out["already_open"], ["icu-EVT-1"])
        self.assertEqual(len(self.icu.oversight), 1)

    def test_acknowledged_alarm_does_not_escalate(self):
        ev = self._fire()
        self.engine.acknowledge(ev["id"], day=0, by="nurse-1")
        out = escalate_events(self.engine, self.icu, facility="phc-1",
                              day=1, opened={})
        self.assertEqual(out["opened"], [])


class TestTimeoutsToReview(unittest.TestCase):
    def setUp(self):
        from steward import TimeoutClock
        from pharmreview import PharmacyReview
        self.clock = TimeoutClock()
        self.clock.start_course("ABX-1", patient_id="p1",
                                drug_code="NG-CIPRO500", route="iv",
                                day=0, indication="pyelonephritis",
                                prescriber="mo-1")
        readers = ReaderRegistry()
        readers.register("pharm-1", licence_id="PCN-1",
                         licence_expiry_day=400,
                         scopes=("clinical-pharmacist",))
        self.pharm = PharmacyReview(readers)

    def test_due_course_lands_in_the_queue_once(self):
        out = timeouts_to_review(self.pharm, self.clock, day=3)
        self.assertEqual(out["due"], 1)
        self.assertEqual(out["newly_queued"], 1)
        self.assertEqual(out["case_ids"], ["timeout-ABX-1-due2"])
        # the second ring of the bell is not a second case
        out = timeouts_to_review(self.pharm, self.clock, day=3)
        self.assertEqual(out["newly_queued"], 0)

    def test_nothing_due_queues_nothing(self):
        out = timeouts_to_review(self.pharm, self.clock, day=1)
        self.assertEqual(out["due"], 0)
        self.assertEqual(out["case_ids"], [])


class TestDeteriorationToICU(unittest.TestCase):
    def setUp(self):
        from earlywarn import EarlyWarner
        self.vitals = VitalsIngest()
        self.ew = EarlyWarner(self.vitals)
        readers = ReaderRegistry()
        readers.register("icu-1", licence_id="MDCN-I1",
                         licence_expiry_day=400, scopes=("intensivist",))
        self.icu = TeleICU(readers)

    def _score(self, pid, day, **kw):
        base = {"respiratory-rate": 16, "spo2": 98, "temperature": 37.0,
                "systolic-bp": 120, "heart-rate": 72}
        base.update(kw)
        for vital, val in base.items():
            self.vitals.record(pid, vital, val, day=day,
                               ts=f"2026-11-02T{8 + day:02d}:00:00Z")
        return self.ew.score_now(pid, day=day, ts=f"t{day}", avpu="A")

    def test_deterioration_opens_oversight_once(self):
        self._score("p1", 0)
        self._score("p1", 1, **{"respiratory-rate": 27, "spo2": 90,
                                "systolic-bp": 88, "heart-rate": 118})
        opened = {}
        out = deterioration_to_icu(self.ew, self.icu, facility="phc-1",
                                   day=1, opened=opened)
        self.assertEqual(out["opened"], ["icu-news2-p1"])
        ov = self.icu.oversight["icu-news2-p1"]
        self.assertTrue(ov["trigger_ref"].startswith("news2:"))
        # rising further does not open a second case
        self._score("p1", 2, **{"respiratory-rate": 30, "spo2": 88,
                                "systolic-bp": 85, "heart-rate": 125})
        out = deterioration_to_icu(self.ew, self.icu, facility="phc-1",
                                   day=2, opened=opened)
        self.assertEqual(out["opened"], [])
        self.assertEqual(out["already_open"], ["icu-news2-p1"])
        self.assertEqual(len(self.icu.oversight), 1)

    def test_stable_patients_open_nothing(self):
        self._score("p2", 0)
        self._score("p2", 1)
        out = deterioration_to_icu(self.ew, self.icu, facility="phc-1",
                                   day=1, opened={})
        self.assertEqual(out["opened"], [])


class TestOrderWithPriors(unittest.TestCase):
    def setUp(self):
        self.pacs = StudyRegistry()
        self.pacs.register_study(study_uid="1.1", accession="A1",
                                 patient_id="p1", modality="CT",
                                 exam_protocol="ct-head",
                                 priority="routine", facility="f", day=0,
                                 instances=30)
        self.pacs.register_study(study_uid="1.2", accession="A2",
                                 patient_id="p1", modality="CT",
                                 exam_protocol="ct-head",
                                 priority="routine", facility="f", day=30,
                                 instances=30)
        readers = ReaderRegistry()
        readers.register("rad-1", licence_id="L1", licence_expiry_day=400,
                         scopes=("chest",))
        self.svc = ReadingService(
            DomainConfig("radiology", scopes=("chest",),
                         sla_hours={"stat": 2, "urgent": 8,
                                    "routine": 24}), readers)

    def test_priors_attached_at_order_time(self):
        out = order_with_priors(self.svc, self.pacs, "CX-1",
                                patient_id="p1", modality="CT",
                                priority="routine",
                                question="?progression", facility="f",
                                referrer="mo-1", day=31)
        self.assertEqual(out["priors"]["count"], 2)
        self.assertEqual([s["study_uid"] for s in
                          out["priors"]["studies"]], ["1.1", "1.2"])
        self.assertEqual(self.svc.cases["CX-1"]["meta"]["priors"]
                         ["study_uids"], ["1.1", "1.2"])

    def test_no_priors_is_stated_not_silent(self):
        out = order_with_priors(self.svc, self.pacs, "CX-2",
                                patient_id="ghost", priority="stat",
                                question="?pneumothorax", facility="f",
                                referrer="mo-1", day=0)
        self.assertEqual(out["priors"]["count"], 0)
        self.assertEqual(self.svc.cases["CX-2"]["meta"]["priors"]
                         ["count"], 0)


class TestScreensToRegister(unittest.TestCase):
    """Seam 11: a screen closes the invitation round; the register
    keeps hunting only the women who have not come."""

    def setUp(self):
        readers = ReaderRegistry()
        readers.register("cx-1", licence_id="L1", licence_expiry_day=400,
                         scopes=("cervix-reviewer",))
        self.cervix = CervixScreening(readers)
        self.reg = CallRecallRegister("cervix")

    def test_screen_closes_round_and_is_idempotent(self):
        # born 30 years before day 0 → eligible, due immediately
        self.reg.enroll("w-1", birth_day=-30 * 365, day=0)
        self.reg.enroll("w-2", birth_day=-30 * 365, day=0)
        self.cervix.screen("w-1", facility="f", screener="s", day=5,
                           modality="hpv-dna")
        marked = {}
        out = screens_to_register(self.cervix, self.reg, day=5,
                                  marked=marked)
        self.assertEqual(out["marked_screened"], ["w-1"])
        self.assertEqual(self.reg.due_day("w-1"), 5 + 30 * 60)
        # second run: the bell does not ring twice
        again = screens_to_register(self.cervix, self.reg, day=6,
                                    marked=marked)
        self.assertEqual(again["marked_screened"], [])
        self.assertEqual(again["already_marked"], ["w-1"])
        # w-2 never came — she is exactly who the ladder is for
        due = self.reg.steps_due(as_of=6)
        self.assertEqual([d["patient_id"] for d in due], ["w-2"])

    def test_out_of_cohort_screen_is_care_not_error(self):
        self.cervix.screen("w-walkin", facility="f", screener="s",
                           day=5, modality="via")
        out = screens_to_register(self.cervix, self.reg, day=5,
                                  marked={})
        self.assertEqual(out["marked_screened"], [])


class TestColpoBiopsyToPath(unittest.TestCase):
    """Seam 12: a biopsy decision opens the histology loop AND the
    custody chain — both, or neither."""

    def setUp(self):
        readers = ReaderRegistry()
        readers.register("cx-1", licence_id="L1", licence_expiry_day=400,
                         scopes=("cervix-reviewer",))
        readers.register("path-1", licence_id="P1",
                         licence_expiry_day=400,
                         scopes=("histopathologist",))
        self.cervix = CervixScreening(readers)
        self.path = PathologyService(readers)
        self.cervix.screen("w-1", facility="f", screener="s", day=0,
                           modality="hpv-dna")
        self.cervix.record_hpv("w-1", result="positive", day=0)
        self.cervix.triage("w-1", method="colposcopy", day=1)
        self.cervix.refer("w-1", destination="colpo-clinic", day=1,
                          reason="hpv-positive triage")

    def test_biopsy_enters_custody_and_loop_opens(self):
        self.cervix.record_colposcopy("w-1", impression="high-grade",
                                      decision="biopsy", day=2,
                                      by="colpo-1")
        out = colpo_biopsy_to_path(self.cervix, self.path, "w-1",
                                   specimen_id="SP-9",
                                   collector="colpo-1", day=2)
        self.assertEqual(out["specimen"], "SP-9")
        self.assertEqual(out["state"], "collected")
        self.assertEqual(self.cervix.cascade["w-1"]["specimen_id"],
                         "SP-9")
        self.assertEqual(self.path.specimens["SP-9"]["site"], "cervix")
        with self.assertRaises(CrosslinkError):  # specimen attached
            colpo_biopsy_to_path(self.cervix, self.path, "w-1",
                                 specimen_id="SP-10",
                                 collector="colpo-1", day=3)

    def test_no_biopsy_decision_no_custody(self):
        self.cervix.record_colposcopy("w-1", impression="normal",
                                      decision="discharge", day=2,
                                      by="colpo-1")
        with self.assertRaises(CrosslinkError):
            colpo_biopsy_to_path(self.cervix, self.path, "w-1",
                                 specimen_id="SP-11",
                                 collector="colpo-1", day=2)


class TestAdmissionsToMedRec(unittest.TestCase):
    """Seam 13: the census drives reconciliation — the transition is
    the moment the med list is lost, so the list is made then."""

    def test_admission_starts_reconciliation_idempotently(self):
        ward = Ward("f1")
        ward.add_bed("B1", "ward-a")
        ward.add_bed("B2", "ward-a")
        ward.admit("p-1", "B1", "nurse-1")
        ward.admit("p-2", "B2", "nurse-1")
        med = MedRec()
        started = set()
        out = admissions_to_medrec(ward, med, day=0, by="clerk-1",
                                   started=started)
        self.assertEqual(sorted(out["started"]), ["p-1", "p-2"])
        again = admissions_to_medrec(ward, med, day=1, by="clerk-1",
                                     started=started)
        self.assertEqual(again["started"], [])
        self.assertEqual(sorted(again["already_started"]),
                         ["p-1", "p-2"])
        # discharge removes from census → no discharge-day restart
        ward.discharge("EP-00001", "nurse-1")
        third = admissions_to_medrec(ward, med, day=2, by="clerk-1",
                                     started=started)
        self.assertEqual(third["started"], [])


if __name__ == "__main__":
    unittest.main()


class TestSynopticSignout(unittest.TestCase):
    """Seam 14: verified pathology read -> SIGNED synoptic; both sides
    validated before either mutates."""

    ANSWERS = {"specimen_type": "right hemicolectomy",
               "tumor_site": "ascending colon",
               "histologic_type": "adenocarcinoma", "grade": "g2",
               "tumor_extent_pt": "pT3", "margins": "negative",
               "lymph_nodes_examined": 14, "lymph_nodes_positive": 1,
               "lymphovascular_invasion": "absent",
               "perineural_invasion": "absent",
               "tnm_stage": "pT3 N1a (AJCC 8th)"}

    def _verified_path_case(self):
        readers = ReaderRegistry()
        readers.register("path-1", licence_id="MDCN-P1",
                         licence_expiry_day=400,
                         scopes=("histopathologist",))
        path = PathologyService(readers)
        path.register_specimen("SP-1", patient_id="p-1",
                               specimen_type="resection", site="colon",
                               collector="surg-1", facility="phc-1",
                               day=0)
        for stage in ("fixed", "processed", "sectioned", "stained",
                      "scanned"):
            path.advance("SP-1", stage, day=1, by="lab-1",
                         fixation_hours=12)
        path.submit_for_reading("SP-1", case_id="PC-1",
                                priority="routine", referrer="surg-1",
                                question="colon ca resection",
                                artifact="wsi", day=1)
        path.reading.assign("PC-1", day=1, prefer="path-1")
        path.reading.draft_report("PC-1", reader_id="path-1",
                                  findings="tumor 3cm",
                                  impression="adenocarcinoma",
                                  template_version="freeform@1", day=2)
        path.reading.verify_report("PC-1", reader_id="path-1", day=2)
        return path

    def test_verified_read_signs_out_as_synoptic(self):
        path = self._verified_path_case()
        rk = ReportKit(signing_key=b"test-key")
        out = synoptic_signout(path, rk, case_id="PC-1",
                               report_id="SYN-1",
                               template_id="cap-colon-resection@1.0.0",
                               answers=dict(self.ANSWERS),
                               signer="path-1", day=3)
        self.assertEqual(out["specimen_id"], "SP-1")
        self.assertEqual(path._spec("SP-1")["synoptic_report"], "SYN-1")
        self.assertTrue(rk.verify("SYN-1")["valid"])

    def test_contradicting_synoptic_refuses_before_mutation(self):
        path = self._verified_path_case()
        rk = ReportKit(signing_key=b"test-key")
        bad = dict(self.ANSWERS, lymph_nodes_examined=2,
                   lymph_nodes_positive=5)
        with self.assertRaises(Exception) as ctx:
            synoptic_signout(path, rk, case_id="PC-1",
                             report_id="SYN-2",
                             template_id="cap-colon-resection@1.0.0",
                             answers=bad, signer="path-1", day=3)
        self.assertIn("cannot find more than you looked at",
                      str(ctx.exception))
        # nothing half-mutated on either side
        self.assertNotIn("SYN-2", rk.reports)
        self.assertIsNone(path._spec("SP-1").get("synoptic_report"))

    def test_unverified_read_cannot_sign_out(self):
        path = self._verified_path_case()
        path.register_specimen("SP-2", patient_id="p-2",
                               specimen_type="biopsy", site="skin",
                               collector="d-1", facility="phc-1", day=0)
        for stage in ("fixed", "processed", "sectioned", "stained",
                      "scanned"):
            path.advance("SP-2", stage, day=1, by="lab-1",
                         fixation_hours=12)
        path.submit_for_reading("SP-2", case_id="PC-2",
                                priority="routine", referrer="d-1",
                                question="skin biopsy",
                                artifact="wsi", day=1)
        rk = ReportKit(signing_key=b"test-key")
        with self.assertRaises(CrosslinkError):
            synoptic_signout(path, rk, case_id="PC-2",
                             report_id="SYN-3",
                             template_id="cap-colon-resection@1.0.0",
                             answers=dict(self.ANSWERS),
                             signer="path-1", day=3)

    def test_no_double_signout(self):
        path = self._verified_path_case()
        rk = ReportKit(signing_key=b"test-key")
        synoptic_signout(path, rk, case_id="PC-1", report_id="SYN-1",
                         template_id="cap-colon-resection@1.0.0",
                         answers=dict(self.ANSWERS), signer="path-1",
                         day=3)
        with self.assertRaises(CrosslinkError):
            synoptic_signout(path, rk, case_id="PC-1",
                             report_id="SYN-4",
                             template_id="cap-colon-resection@1.0.0",
                             answers=dict(self.ANSWERS),
                             signer="path-1", day=4)


class TestDecisionToOrders(unittest.TestCase):
    """Seam 15: a board decision to treat lands as real screened,
    placed orders — or the action stays an open loop."""

    def _decided_board(self):
        from mdt import TumourBoard
        from reportkit import ReportKit
        b = TumourBoard("tb")
        b.add_member("chair-1", role="chair", specialty="oncology")
        b.add_member("onc-1", role="member", specialty="oncology")
        b.add_member("rad-1", role="member", specialty="radiology")
        rk = ReportKit(signing_key=b"k")
        rk.draft("R-1", template_id="ct-head-acute@1.0.0",
                 answers={"technique": "t", "hemorrhage": "no",
                          "midline_shift": "no", "mass_effect": "no",
                          "acute_infarct": "no", "impression": "i"},
                 case_id="CX-9", author_id="rad-1", day=0)
        rk.sign("R-1", signer_id="rad-1", day=0)
        b.submit_case("MDT-C1", patient_id="p-ca",
                      question="treat?",
                      evidence_refs=["report:R-1@signed"],
                      submitted_by="chair-1", day=0)
        b.hold_meeting("M-1", day=1, chair="chair-1",
                       attendees=["chair-1", "onc-1", "rad-1"],
                       case_ids=["MDT-C1"])
        b.record_decision("M-1", "MDT-C1", day=1,
                          decision="start FOLFOX",
                          actions=["start chemo", "book chair"],
                          recorder="onc-1")
        return b

    def _orders(self):
        from orders import OrderService
        return OrderService()

    def test_decision_becomes_placed_orders(self):
        b = self._decided_board()
        o = self._orders()
        out = decision_to_orders(
            b, o, "MDT-C1", prescriber_id="onc-1", day=2,
            prescriptions=[("NG-PARA500", 1000, 3, 5)])
        self.assertEqual(len(out["orders"]), 1)
        self.assertEqual(o.orders[out["orders"][0]].status, "placed")
        self.assertEqual(o.orders[out["orders"][0]].indication,
                         "mdt-decision:MDT-C1")
        self.assertEqual(b._case("MDT-C1")["orders"], out["orders"])

    def test_undecided_case_cannot_order(self):
        b = self._decided_board()
        b.submit_case("MDT-C2", patient_id="p-ca2", question="treat?",
                      evidence_refs=["report:R-1@signed"],
                      submitted_by="chair-1", day=0)
        with self.assertRaises(CrosslinkError):
            decision_to_orders(b, self._orders(), "MDT-C2",
                               prescriber_id="onc-1", day=2,
                               prescriptions=[("NG-PARA500", 1000, 3, 5)])

    def test_allergy_refuses_before_any_draft(self):
        b = self._decided_board()
        o = self._orders()
        with self.assertRaises(Exception):
            decision_to_orders(
                b, o, "MDT-C1", prescriber_id="onc-1", day=2,
                prescriptions=[("NG-AMOX500", 500, 3, 7)],
                patient_allergies=["penicillin"])
        self.assertEqual(o.orders, {})  # nothing drafted at all

    def test_major_findings_block_the_whole_batch(self):
        b = self._decided_board()
        o = self._orders()
        # lisinopril + ibuprofen = major interaction in one batch
        with self.assertRaises(CrosslinkError):
            decision_to_orders(
                b, o, "MDT-C1", prescriber_id="onc-1", day=2,
                prescriptions=[("NG-LISIN10", 10, 1, 30),
                               ("NG-IBU400", 400, 3, 5)])
        for od in o.orders.values():
            self.assertNotEqual(od.status, "placed")
        self.assertIsNone(b._case("MDT-C1").get("orders"))

    def test_override_places_with_reasons_recorded(self):
        b = self._decided_board()
        o = self._orders()
        out = decision_to_orders(
            b, o, "MDT-C1", prescriber_id="onc-1", day=2,
            prescriptions=[("NG-LISIN10", 10, 1, 30),
                           ("NG-IBU400", 400, 3, 5)],
            override_reasons=["board weighs benefit > risk; renal "
                              "monitoring booked"])
        for oid in out["orders"]:
            self.assertEqual(o.orders[oid].status, "placed")
        # the order that screened against the other batch member
        # carries the documented override
        with_overrides = [oid for oid in out["orders"]
                          if o.orders[oid].overrides]
        self.assertTrue(with_overrides)
        self.assertIn("board weighs benefit",
                      o.orders[with_overrides[0]].overrides[0]
                      ["reason"])

    def test_unknown_code_refuses_before_mutation(self):
        b = self._decided_board()
        o = self._orders()
        with self.assertRaises(CrosslinkError):
            decision_to_orders(b, o, "MDT-C1", prescriber_id="onc-1",
                               day=2,
                               prescriptions=[("NG-NOPE", 1, 1, 1)])
        self.assertEqual(o.orders, {})


class TestVerifyWithPixels(unittest.TestCase):
    """Seam 16: a report signed without the pixels is an opinion about
    a filename — verification requires a launch session by the
    verifying reader for the case's artifact."""

    def setUp(self):
        self.pacs = StudyRegistry()
        self.pacs.register_study(study_uid="ST-1", accession="A1",
                                 patient_id="p1", modality="CT",
                                 exam_protocol="ct-head",
                                 priority="routine", facility="f", day=0,
                                 instances=30)
        readers = ReaderRegistry()
        readers.register("rad-1", licence_id="L1", licence_expiry_day=400,
                         scopes=("chest",))
        readers.register("rad-2", licence_id="L2", licence_expiry_day=400,
                         scopes=("chest",))
        self.svc = ReadingService(
            DomainConfig("radiology", scopes=("chest",),
                         sla_hours={"stat": 2, "urgent": 8,
                                    "routine": 24}), readers)
        self.launcher = ViewerLauncher(self.pacs,
                                       viewer_base="https://viewer.ex")
        self.svc.order("RC-1", priority="routine",
                       question="?bleed", facility="f", referrer="mo-1",
                       day=1, meta={"patient_id": "p1"})
        self.svc.assign("RC-1", day=1, prefer="rad-1")
        self.svc.draft_report("RC-1", reader_id="rad-1",
                              findings="no bleed", impression="normal",
                              template_version="freeform@1", day=1)

    def test_verify_requires_launch_session(self):
        with self.assertRaises(CrosslinkError) as ctx:
            verify_with_pixels(self.svc, self.launcher, "RC-1",
                               artifact="ST-1", reader_id="rad-1", day=2)
        self.assertIn("never opened", str(ctx.exception))
        self.assertEqual(self.svc.reports["RC-1"]["state"], "draft")

    def test_launch_then_verify_lands_session_on_report(self):
        self.launcher.launch_study("ST-1", reader_id="rad-1",
                                   purpose="dx", day=1, case_id="RC-1")
        out = verify_with_pixels(self.svc, self.launcher, "RC-1",
                                 artifact="ST-1", reader_id="rad-1",
                                 day=2)
        self.assertEqual(out["state"], "verified")
        self.assertEqual(out["viewer_session"], "VS-1")

    def test_someone_elses_session_does_not_count(self):
        self.launcher.launch_study("ST-1", reader_id="rad-2",
                                   purpose="second look", day=1)
        with self.assertRaises(CrosslinkError):
            verify_with_pixels(self.svc, self.launcher, "RC-1",
                               artifact="ST-1", reader_id="rad-1", day=2)

    def test_wrong_artifact_session_does_not_count(self):
        self.pacs.register_study(study_uid="ST-2", accession="A2",
                                 patient_id="p1", modality="CT",
                                 exam_protocol="ct-head",
                                 priority="routine", facility="f", day=0,
                                 instances=12)
        self.launcher.launch_study("ST-2", reader_id="rad-1",
                                   purpose="dx", day=1)
        with self.assertRaises(CrosslinkError):
            verify_with_pixels(self.svc, self.launcher, "RC-1",
                               artifact="ST-1", reader_id="rad-1", day=2)
