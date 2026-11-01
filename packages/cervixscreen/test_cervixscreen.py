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


if __name__ == "__main__":
    unittest.main()
