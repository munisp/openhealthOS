#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packs"))
from protocolpack import Session, ScopeError  # noqa: E402
from ng_standing_orders import PACKS  # noqa: E402

class TestIMNCI(unittest.TestCase):
    def setUp(self):
        self.pack = PACKS["imnci-under5"]

    def test_danger_sign_escalates_immediately(self):
        s = Session(self.pack, "chw")
        s.question()
        out = s.answer("yes")
        self.assertTrue(out["escalate"])
        self.assertEqual(out["outcome"], "emergency-refer-pre-referral-treatment")

    def test_full_well_child_pathway(self):
        s = Session(self.pack, "chw")
        s.question(); s.answer("no")
        s.question(); s.answer("normal")
        s.question(); s.answer("no-diarrhoea")
        out = s.answer("well")
        self.assertEqual(out["outcome"], "routine-prevention-visit")

    def test_cadre_scope_enforced(self):
        s = Session(self.pack, "chw")
        s.perform("give-ors-zinc")  # allowed
        with self.assertRaises(ScopeError):
            s.perform("start-iv-fluids")  # nurse-only
        with self.assertRaises(ScopeError):
            s.perform("manage-severe-pneumonia")  # doctor-only
        denials = [e for e in s.path if e[0] == "ACTION-DENIED"]
        self.assertEqual(len(denials), 2)

    def test_malnutrition_red_flag_referral(self):
        s = Session(self.pack, "nurse")
        s.question(); s.answer("no")
        s.question(); s.answer("normal")
        s.question(); s.answer("no-diarrhoea")
        out = s.answer("oedema-or-muac<11.5")
        self.assertTrue(out["escalate"])
        self.assertIn("malnutrition", out["outcome"])

if __name__ == "__main__":
    unittest.main()
