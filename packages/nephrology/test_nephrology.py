import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from nephrology import DialysisUnit, NephroError, ckd_stage


class TestNephro(unittest.TestCase):
    def setUp(self):
        self.u = DialysisUnit("renal-1")
        self.u.register_patient("p1", dry_weight_kg=65, egfr=8, day=0)
        self.u.register_machine("m1")
        self.u.register_machine("m-hbv", hbv_dedicated=True)
        self.u.record_water_test("chloramine", day=0, result="pass")
        self.u.record_water_test("endotoxin", day=0, result="pass")

    def test_ckd_staging_fail_closed(self):
        self.assertEqual(ckd_stage(8), "G5")
        self.assertEqual(ckd_stage(95), "G1")
        with self.assertRaises(NephroError):
            ckd_stage(0)
        with self.assertRaises(NephroError):
            ckd_stage(200)

    def test_session_happy_path_and_reuse_cap(self):
        out = self.u.start_session("s1", patient_id="p1", machine_id="m1",
                                   dialyzer_id="d1", day=1)
        self.assertEqual(out["flags"], [])
        self.assertEqual(out["dialyzer_use"], 1)

    def test_stale_water_flags_never_blocks(self):
        out = self.u.start_session("s1", patient_id="p1", machine_id="m1",
                                   dialyzer_id="d1", day=100)
        self.assertTrue(any("water-chloramine-stale" in f
                            for f in out["flags"]))
        self.assertTrue(any("water-endotoxin-stale" in f
                            for f in out["flags"]))

    def test_hbv_segregation_enforced(self):
        self.u.record_serology("p1", results={"hbv": "positive"}, day=1)
        with self.assertRaises(NephroError):
            self.u.start_session("s1", patient_id="p1", machine_id="m1",
                                 dialyzer_id="d1", day=2)
        out = self.u.start_session("s1", patient_id="p1",
                                   machine_id="m-hbv", dialyzer_id="d1",
                                   day=2)
        self.assertEqual(out["state"], "running")

    def test_seroconversion_is_an_event(self):
        self.u.record_serology("p1", results={"hcv": "negative"}, day=0)
        self.u.record_serology("p1", results={"hcv": "positive"}, day=90)
        self.assertEqual(self.u.events[0]["event"],
                         "seroconversion-hcv")
        self.assertIn("outbreak", self.u.events[0]["action"])

    def test_serology_due(self):
        due = self.u.serology_due(as_of=10)
        self.assertEqual(due[0]["due"], ["hbv", "hcv", "hiv"])
        self.u.record_serology("p1", results={"hbv": "negative",
                                              "hcv": "negative",
                                              "hiv": "negative"}, day=10)
        self.assertEqual(self.u.serology_due(as_of=50), [])
        self.assertEqual(len(self.u.serology_due(as_of=200)), 1)

    def test_missed_sessions_overdue_first(self):
        # p2 registers later, so p1 (19d since session) outranks p2 (10d,
        # never dialyzed) — sort is purely by days overdue.
        self.u.register_patient("p2", dry_weight_kg=70, egfr=12, day=10)
        self.u.start_session("s1", patient_id="p1", machine_id="m1",
                             dialyzer_id="d1", day=1)
        missed = self.u.missed_sessions(as_of=20)
        self.assertEqual(missed[0]["patient_id"], "p1")
        self.assertEqual(missed[0]["days_since_session"], 19)
        self.assertTrue(missed[1]["never_dialyzed"])

    def test_negative_uf_refused(self):
        self.u.start_session("s1", patient_id="p1", machine_id="m1",
                             dialyzer_id="d1", day=1)
        with self.assertRaises(NephroError):
            self.u.end_session("s1", pre_weight=66, post_weight=67, day=1)
        out = self.u.end_session("s1", pre_weight=67, post_weight=65,
                                 day=1, spktv=1.4)
        self.assertEqual(out["state"], "completed")

    def test_adequacy_denominators(self):
        self.u.start_session("s1", patient_id="p1", machine_id="m1",
                             dialyzer_id="d1", day=1)
        self.u.end_session("s1", pre_weight=67, post_weight=65, day=1,
                           spktv=1.1)
        rep = self.u.adequacy_report()
        self.assertEqual((rep["sessions_with_ktv"], rep["adequate"]),
                         (1, 0))

    def test_access_and_catheter_days(self):
        self.u.register_access("p1", kind="catheter", day=0)
        self.u.fail_access("p1", day=30, reason="infection")
        self.assertEqual(self.u.catheter_days(100)["catheter_days"], 30)

    def test_workup_sequential(self):
        self.u.advance_workup("p1", "referral", day=0, by="dr-1")
        with self.assertRaises(NephroError):
            self.u.advance_workup("p1", "listed", day=1, by="dr-1")
        for stage in ("labs", "imaging", "crossmatch", "listed"):
            self.u.advance_workup("p1", stage, day=2, by="dr-1")
        self.assertEqual(self.u.workups["p1"]["stage"], "listed")

    def test_reuse_cap_refuses(self):
        u = DialysisUnit("renal-2", max_reuse=2)
        u.register_patient("p9", dry_weight_kg=60, egfr=5, day=0)
        u.register_machine("m1")
        for i in range(2):
            u.start_session(f"s{i}", patient_id="p9", machine_id="m1",
                            dialyzer_id="d1", day=i)
        with self.assertRaises(NephroError):
            u.start_session("s3", patient_id="p9", machine_id="m1",
                            dialyzer_id="d1", day=3)


if __name__ == "__main__":
    unittest.main()


class TestPrescriptions(unittest.TestCase):
    def setUp(self):
        self.u = DialysisUnit("renal-1")
        self.u.register_patient("p1", dry_weight_kg=65, egfr=8, day=0)
        self.u.register_machine("m1")
        self.u.record_water_test("chloramine", day=0, result="pass")
        self.u.record_water_test("endotoxin", day=0, result="pass")

    def test_prescription_versioned_never_edited(self):
        r1 = self.u.set_prescription(
            "p1", day=1, by="neph-1", target_uf_ml=2000,
            blood_flow_ml_min=300, duration_min=240,
            dialysate_potassium_mmol=2)
        r2 = self.u.set_prescription(
            "p1", day=8, by="neph-1", target_uf_ml=2500,
            blood_flow_ml_min=320, duration_min=240,
            dialysate_potassium_mmol=2)
        self.assertEqual((r1["version"], r2["version"]), (1, 2))
        self.assertEqual(r2["supersedes"], 1)
        self.assertEqual(self.u.current_prescription("p1")["version"], 2)

    def test_required_fields_and_bounds_fail_closed(self):
        with self.assertRaises(NephroError):      # missing duration
            self.u.set_prescription("p1", day=1, by="n",
                                    target_uf_ml=2000,
                                    blood_flow_ml_min=300)
        with self.assertRaises(NephroError):      # implausible flow
            self.u.set_prescription("p1", day=1, by="n",
                                    target_uf_ml=2000,
                                    blood_flow_ml_min=900,
                                    duration_min=240)
        with self.assertRaises(NephroError):      # unknown field
            self.u.set_prescription("p1", day=1, by="n",
                                    target_uf_ml=2000,
                                    blood_flow_ml_min=300,
                                    duration_min=240, magic_knob=7)

    def test_session_binds_prescription_or_flags(self):
        self.u.set_prescription("p1", day=1, by="neph-1",
                                target_uf_ml=2000, blood_flow_ml_min=300,
                                duration_min=240)
        flagged = self.u.start_session("s1", patient_id="p1",
                                       machine_id="m1", dialyzer_id="d1",
                                       day=2)
        self.assertIn("no-prescription-bound", flagged["flags"])
        bound = self.u.start_session("s2", patient_id="p1",
                                     machine_id="m1", dialyzer_id="d2",
                                     day=3, prescription_version=1)
        self.assertEqual(bound["flags"], [])
        with self.assertRaises(NephroError):      # ghost version
            self.u.start_session("s3", patient_id="p1", machine_id="m1",
                                 dialyzer_id="d3", day=4,
                                 prescription_version=9)

    def test_intradialytic_event_carries_action(self):
        self.u.start_session("s1", patient_id="p1", machine_id="m1",
                             dialyzer_id="d1", day=1)
        with self.assertRaises(NephroError):      # no action
            self.u.record_intradialytic("s1", event="hypotension",
                                        minute=90, action="", day=1,
                                        by="nurse-1")
        ev = self.u.record_intradialytic("s1", event="hypotension",
                                         minute=90,
                                         action="reduced UF, 250ml "
                                         "saline, trendelenburg",
                                         day=1, by="nurse-1")
        self.assertEqual(ev["event"], "hypotension")
        self.u.end_session("s1", pre_weight=67, post_weight=65, day=1)
        with self.assertRaises(NephroError):      # session closed
            self.u.record_intradialytic("s1", event="cramps", minute=200,
                                        action="stretch", day=1,
                                        by="nurse-1")

    def test_intradialytic_report_denominators(self):
        for i in range(4):
            self.u.start_session(f"s{i}", patient_id="p1",
                                 machine_id="m1", dialyzer_id=f"d{i}",
                                 day=i + 1)
            self.u.end_session(f"s{i}", pre_weight=67, post_weight=65,
                               day=i + 1)
        self.u.start_session("s9", patient_id="p1", machine_id="m1",
                             dialyzer_id="d9", day=9)
        self.u.record_intradialytic("s9", event="hypotension", minute=60,
                                    action="reduced UF", day=9,
                                    by="n1")
        rep = self.u.intradialytic_report("p1")
        self.assertEqual(rep["sessions"], 5)
        self.assertEqual(rep["hypotension_per_100_sessions"], 20.0)


class TestAnemia(unittest.TestCase):
    def setUp(self):
        self.u = DialysisUnit("renal-1")
        self.u.register_patient("p1", dry_weight_kg=65, egfr=8, day=0)

    def test_labs_fail_closed(self):
        with self.assertRaises(NephroError):
            self.u.record_anemia_labs("p1", hb_g_dl=25, tsat_pct=30,
                                      ferritin_ng_ml=300, day=1)
        with self.assertRaises(NephroError):
            self.u.record_anemia_labs("p1", hb_g_dl=9.5, tsat_pct=95,
                                      ferritin_ng_ml=300, day=1)

    def test_iron_before_esa(self):
        self.u.record_anemia_labs("p1", hb_g_dl=9.0, tsat_pct=15,
                                  ferritin_ng_ml=120, day=1)
        rec = self.u.anemia_review("p1", day=2)
        self.assertEqual(rec["recommendation"]["action"],
                         "iv-iron-first")
        self.assertTrue(rec["draft"])

    def test_esa_refused_above_harm_threshold(self):
        self.u.record_anemia_labs("p1", hb_g_dl=12.0, tsat_pct=30,
                                  ferritin_ng_ml=400, day=1)
        rec = self.u.anemia_review("p1", day=2)
        self.assertEqual(rec["recommendation"]["action"], "hold-esa")
        with self.assertRaises(NephroError):
            self.u.administer_esa("p1", dose_iu=4000, day=2, by="n1",
                                  review_day=2)

    def test_start_esa_when_replete_and_low(self):
        self.u.record_anemia_labs("p1", hb_g_dl=9.2, tsat_pct=30,
                                  ferritin_ng_ml=400, day=1)
        rec = self.u.anemia_review("p1", day=2)
        self.assertEqual(rec["recommendation"]["action"], "start-esa")
        out = self.u.administer_esa("p1", dose_iu=4000, day=2, by="n1",
                                    review_day=2)
        self.assertEqual(out["dose_iu"], 4000)

    def test_fast_rise_forces_reduction(self):
        self.u.record_anemia_labs("p1", hb_g_dl=9.0, tsat_pct=30,
                                  ferritin_ng_ml=400, day=0)
        self.u.administer_esa("p1", dose_iu=4000, day=0, by="n1",
                              review_day=0)
        self.u.record_anemia_labs("p1", hb_g_dl=11.2, tsat_pct=32,
                                  ferritin_ng_ml=420, day=14)
        rec = self.u.anemia_review("p1", day=14)
        self.assertEqual(rec["recommendation"]["action"], "reduce-esa")

    def test_review_requires_labs_and_esa_requires_review(self):
        with self.assertRaises(NephroError):
            self.u.anemia_review("p1", day=1)
        with self.assertRaises(NephroError):
            self.u.administer_esa("p1", dose_iu=4000, day=1, by="n1",
                                  review_day=1)
        self.u.record_anemia_labs("p1", hb_g_dl=9.0, tsat_pct=30,
                                  ferritin_ng_ml=300, day=5)
        with self.assertRaises(NephroError):  # review after the dose?!
            self.u.administer_esa("p1", dose_iu=4000, day=1, by="n1",
                                  review_day=2)

    def test_esa_response_report_denominators(self):
        self.u.record_anemia_labs("p1", hb_g_dl=9.0, tsat_pct=30,
                                  ferritin_ng_ml=300, day=0)
        self.u.administer_esa("p1", dose_iu=4000, day=1, by="n1",
                              review_day=0)
        self.u.record_anemia_labs("p1", hb_g_dl=10.8, tsat_pct=35,
                                  ferritin_ng_ml=350, day=30)
        rep = self.u.esa_response_report()
        self.assertEqual(rep["patients_on_esa"], 1)
        self.assertEqual(rep["hb_in_target"], 1)


class TestCKDMBD(unittest.TestCase):
    def setUp(self):
        self.u = DialysisUnit("renal-1")
        self.u.register_patient("p1", dry_weight_kg=65, egfr=8, day=0)

    def test_mbd_labs_and_drafts(self):
        self.u.record_mbd_labs("p1", calcium_mmol=2.2,
                               phosphate_mmol=2.1, pth_pmol_l=50, day=0)
        self.u.record_mbd_labs("p1", calcium_mmol=2.25,
                               phosphate_mmol=2.3, pth_pmol_l=75, day=30)
        out = self.u.mbd_review("p1", day=30)
        kinds = [d["draft"] for d in out["drafts"]]
        self.assertIn("binder-review", kinds)
        self.assertIn("vitamin-d-review", kinds)
        self.assertIn("pth-trend-rising", kinds)

    def test_mbd_implausible_refused(self):
        with self.assertRaises(NephroError):
            self.u.record_mbd_labs("p1", calcium_mmol=9.0,
                                   phosphate_mmol=1.5, pth_pmol_l=40,
                                   day=0)

    def test_mbd_review_requires_labs(self):
        with self.assertRaises(NephroError):
            self.u.mbd_review("p1", day=0)


class TestPeritonealDialysis(unittest.TestCase):
    def setUp(self):
        self.u = DialysisUnit("renal-1")
        self.u.register_patient("p1", dry_weight_kg=65, egfr=8, day=0)
        self.u.register_pd("p1", modality="capd", day=0)

    def test_pd_registration_gates(self):
        with self.assertRaises(NephroError):
            self.u.register_pd("p1", modality="apd", day=1)   # dup
        with self.assertRaises(NephroError):
            self.u.register_pd("p2", modality="apd", day=1)   # unknown
        self.u.register_patient("p2", dry_weight_kg=70, egfr=9, day=0)
        with self.assertRaises(NephroError):
            self.u.register_pd("p2", modality="night-cycler", day=1)

    def test_pet_transporter_category(self):
        pet = self.u.record_pet("p1", d_p_creatinine=0.72, day=30)
        self.assertEqual(pet["category"], "high-average")
        with self.assertRaises(NephroError):
            self.u.record_pet("p1", d_p_creatinine=1.4, day=30)
        self.u.register_patient("p3", dry_weight_kg=70, egfr=9, day=0)
        with self.assertRaises(NephroError):  # not on PD
            self.u.record_pet("p3", d_p_creatinine=0.6, day=30)

    def test_peritonitis_confirmed_carries_protocol_draft(self):
        ep = self.u.record_peritonitis("p1", day=60,
                                       cloudy_effluent=True,
                                       wbc_per_ul=450, by="n1")
        self.assertTrue(ep["confirmed"])
        self.assertEqual(ep["draft"]["action"], "ip-antibiotics-empiric")

    def test_peritonitis_ruled_out_is_honest(self):
        ep = self.u.record_peritonitis("p1", day=60,
                                       cloudy_effluent=False,
                                       wbc_per_ul=40, by="n1")
        self.assertFalse(ep["confirmed"])
        self.assertIsNone(ep["draft"])

    def test_peritonitis_rate_denominator_is_patient_years(self):
        self.u.register_patient("p2", dry_weight_kg=70, egfr=9, day=0)
        self.u.register_pd("p2", modality="apd", day=0)
        # 2 patients × 365.25 days ≈ 2 patient-years; 1 episode → 0.5/yr
        self.u.record_peritonitis("p1", day=365, cloudy_effluent=True,
                                  wbc_per_ul=300, by="n1")
        rep = self.u.peritonitis_report(as_of=365)
        self.assertEqual(rep["episodes"], 1)
        self.assertAlmostEqual(rep["rate_per_patient_year"], 0.5,
                               places=1)
