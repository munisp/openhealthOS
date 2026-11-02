import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "teleread"))
sys.path.insert(0, os.path.dirname(__file__))
from teleread import ReaderRegistry, TeleReadError
from cervixscreen import CervixScreening


class TestCervix(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("cx-1", licence_id="CX1", licence_expiry_day=400,
                   scopes=("cervix-reviewer",))
        self.svc = CervixScreening(r)

    def test_cannot_treat_negative(self):
        self.svc.screen("p1", facility="f", screener="n1", day=0)
        self.svc.record_via("p1", result="negative", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.treat("p1", method="thermal-ablation", day=0)

    def test_positive_treated_same_visit(self):
        self.svc.screen("p1", facility="f", screener="n1", day=0)
        self.svc.record_via("p1", result="positive", day=0)
        out = self.svc.treat("p1", method="thermal-ablation", day=0)
        self.assertTrue(out["treatment"]["same_visit"])
        stats = self.svc.program_stats()
        self.assertEqual(stats["treated_same_visit"], 1)

    def test_suspicious_goes_to_referral_not_treatment(self):
        self.svc.screen("p2", facility="f", screener="n1", day=0)
        self.svc.record_via("p2", result="suspicious-of-cancer", day=0)
        with self.assertRaises(TeleReadError):
            self.svc.treat("p2", method="thermal-ablation", day=0)
        self.svc.refer("p2", destination="teaching-hospital", day=1,
                       reason="biopsy required")

    def test_open_loops_overdue_first(self):
        self.svc.screen("p3", facility="f", screener="n1", day=0)
        self.svc.record_via("p3", result="positive", day=0)
        loops = self.svc.open_loops(as_of=10)
        self.assertEqual(len(loops), 1)
        self.assertEqual(loops[0]["days_open"], 10)
        self.assertEqual(self.svc.open_loops(as_of=3), [])

    def test_image_triggers_overread_case(self):
        out = self.svc.screen("p4", facility="f", screener="n1", day=0,
                              image_ref="cerv-1")
        self.assertIsNotNone(out["overread_case"])


class TestHPVPathway(unittest.TestCase):
    def setUp(self):
        r = ReaderRegistry()
        r.register("cx-1", licence_id="C1", licence_expiry_day=400,
                   scopes=("cervix-reviewer",))
        self.svc = CervixScreening(r)
        self.svc.screen("w1", facility="f", screener="n1", day=0,
                        modality="hpv-dna")
        self.svc.screen("w2", facility="f", screener="n1", day=0,
                        modality="hpv-dna", hiv_positive=True)
        self.svc.screen("w3", facility="f", screener="n1", day=0,
                        modality="via")

    def test_hpv_only_on_hpv_pathway(self):
        with self.assertRaises(TeleReadError):
            self.svc.record_hpv("w3", result="negative", day=1)
        with self.assertRaises(TeleReadError):
            self.svc.record_hpv("w1", result="borderline", day=1)

    def test_negative_books_recall_by_hiv_status(self):
        out = self.svc.record_hpv("w1", result="negative", day=1)
        self.assertEqual(out["recall"]["interval_months"], 60)
        self.assertEqual(out["recall"]["due_day"], 1 + 1800)
        out2 = self.svc.record_hpv("w2", result="negative", day=1)
        self.assertEqual(out2["recall"]["interval_months"], 36)

    def test_valid_result_stands_invalid_retests(self):
        self.svc.record_hpv("w1", result="invalid", day=1)
        self.assertTrue(self.svc.cascade["w1"]["awaiting_retest"])
        self.svc.record_hpv("w1", result="negative", day=3)  # retest ok
        self.assertFalse(self.svc.cascade["w1"]["awaiting_retest"])
        with self.assertRaises(TeleReadError):  # a valid result stands
            self.svc.record_hpv("w1", result="positive", day=4)

    def test_positive_needs_triage_then_via_or_colposcopy(self):
        self.svc.record_hpv("w1", result="positive", day=1)
        with self.assertRaises(TeleReadError):  # treat without triage+via
            self.svc.treat("w1", method="thermal-ablation", day=1)
        with self.assertRaises(TeleReadError):
            self.svc.triage("w1", method="guesswork", day=1)
        self.svc.triage("w1", method="via", day=2)
        self.svc.record_via("w1", result="positive", day=2)
        out = self.svc.treat("w1", method="thermal-ablation", day=2)
        self.assertTrue(out["treatment"]["same_visit"])
        # w2: HPV+ triaged to colposcopy → referral lawful
        self.svc.record_hpv("w2", result="positive", day=1)
        self.svc.triage("w2", method="colposcopy", day=2)
        with self.assertRaises(TeleReadError):  # no destination
            self.svc.refer("w2", destination="", day=2,
                           reason="hpv16+ colpo")
        ref = self.svc.refer("w2", destination="lagos-teaching", day=2,
                             reason="hpv-positive, colposcopy triage")
        self.assertTrue(ref["referred"])

    def test_hpv_positive_via_negative_recalled_12m(self):
        self.svc.record_hpv("w1", result="positive", day=1)
        self.svc.triage("w1", method="via", day=2)
        self.svc.record_via("w1", result="negative", day=2)
        rec = self.svc.cascade["w1"]["recall"]
        self.assertEqual(rec["interval_months"], 12)
        due = self.svc.hpv_recall_due(as_of=2 + 360)
        self.assertEqual(due[0]["patient_id"], "w1")

    def test_open_loops_catch_untriaged_positive_and_invalid(self):
        self.svc.record_hpv("w1", result="positive", day=1)
        self.svc.record_hpv("w2", result="invalid", day=2)
        loops = self.svc.open_loops(as_of=20)
        kinds = {l["patient_id"]: l["result"] for l in loops}
        self.assertEqual(kinds["w1"], "hpv-positive-awaiting-triage")
        self.assertEqual(kinds["w2"], "hpv-invalid-awaiting-retest")
        self.assertEqual(loops[0]["patient_id"], "w1")  # older first

    def test_program_stats_hpv_denominators(self):
        self.svc.record_hpv("w1", result="positive", day=1)
        self.svc.record_hpv("w2", result="negative", day=1)
        stats = self.svc.program_stats()
        self.assertEqual(stats["hpv_tested"], 2)
        self.assertEqual(stats["hpv_positive"], 1)
        self.assertEqual(stats["hpv_positivity_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
