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
