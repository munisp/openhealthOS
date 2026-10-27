import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packs"))
sys.path.insert(0, os.path.dirname(__file__))

from protocolpack import Session, ScopeError
from ng_psbi import PSBI_V1


class TestPSBI(unittest.TestCase):
    def setUp(self):
        self.pack = PSBI_V1

    def test_danger_sign_escalates_with_outcome(self):
        s = Session(self.pack, "chw")
        s.question()
        out = s.answer("yes")
        self.assertTrue(out["escalate"])
        self.assertIn("emergency-refer", out["outcome"])
        self.assertTrue(out["done"])

    def test_psbi_signs_escalate(self):
        s = Session(self.pack, "chw")
        s.answer("no")
        out = s.answer("yes")
        self.assertTrue(out["escalate"])
        self.assertIn("psbi", out["outcome"])

    def test_well_newborn_pathway(self):
        s = Session(self.pack, "chw")
        s.answer("no")
        s.answer("no")
        s.answer("feeding-well")
        out = s.answer("none")
        self.assertEqual(out["outcome"], "routine-newborn-care-followup")
        self.assertFalse(out["escalate"])

    def test_severe_jaundice_escalates(self):
        s = Session(self.pack, "nurse")
        s.answer("no")
        s.answer("no")
        s.answer("feeding-well")
        out = s.answer("severe-jaundice")
        self.assertTrue(out["escalate"])
        self.assertEqual(out["outcome"], "emergency-refer-jaundice")

    def test_cadre_scope(self):
        s = Session(self.pack, "chw")
        s.perform("teach-express-breastmilk-cup-feed")  # allowed
        with self.assertRaises(ScopeError):
            s.perform("give-im-gentamicin-pre-referral")  # nurse-only
        denials = [e for e in s.path if e[0] == "ACTION-DENIED"]
        self.assertEqual(denials, [("ACTION-DENIED",
                                    "give-im-gentamicin-pre-referral")])


if __name__ == "__main__":
    unittest.main()
