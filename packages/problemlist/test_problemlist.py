"""Dedicated suite for problemlist.py — the clinical spine."""
import unittest
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import problemlist as pl


class TestProblemList(unittest.TestCase):
    def test_icd10_validation(self):
        self.assertTrue(pl._ICD10.match("J06"))
        self.assertTrue(pl._ICD10.match("E11.65"))
        self.assertFalse(pl._ICD10.match("not-a-code"))
        self.assertFalse(pl._ICD10.match("123"))

    def test_chronicity_threshold(self):
        self.assertEqual(pl.CHRONIC_DAYS, 90)

    def test_states_include_recurrence(self):
        # a problem never disappears — it changes state
        for s in ("active", "resolved", "ruled-out", "recurrence"):
            self.assertIn(s, pl.STATES)

    def test_version_stamped(self):
        self.assertTrue(pl.VERSION.startswith("problemlist@"))


if __name__ == "__main__":
    unittest.main()
