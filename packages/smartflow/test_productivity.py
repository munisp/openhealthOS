"""Tests: learned defaults, anomaly whispers, restock drafts."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from defaults import DefaultsLearner, MIN_EVIDENCE  # noqa: E402
from whisper import Whisperer  # noqa: E402
import restock  # noqa: E402

DAY = 86400


class TestDefaults(unittest.TestCase):
    def test_dominant_value_prefills_with_evidence(self):
        d = DefaultsLearner()
        for i in range(10):
            d.observe("dispense:malaria", "dosage_unit", "tablet")
            if i < 2:
                d.observe("dispense:malaria", "dosage_unit", "syrup")
        s = d.default("dispense:malaria", "dosage_unit")
        self.assertEqual(s["state"], "suggested")
        self.assertEqual(s["value"], "tablet")
        self.assertIn("of last", s["evidence"])

    def test_insufficient_evidence_is_honest(self):
        d = DefaultsLearner()
        d.observe("ctx", "ward", "general")
        s = d.default("ctx", "ward")
        self.assertEqual(s["state"], "insufficient-evidence")
        self.assertEqual(s["observations"], 1)

    def test_no_dominance_no_default(self):
        d = DefaultsLearner()
        for v in ["a", "b", "a", "b", "c"]:
            d.observe("ctx", "field", v)
        self.assertEqual(d.default("ctx", "field")["state"],
                         "no-dominant-value")

    def test_free_text_refused_and_skipped(self):
        d = DefaultsLearner(free_text_fields={"note"})
        with self.assertRaises(ValueError):
            d.observe("ctx", "note", "patient said...")
        pre = d.prefill("ctx", ["note"])
        self.assertEqual(pre, {})  # free text stays manual, silently

    def test_correction_shifts_dominance(self):
        d = DefaultsLearner()
        for _ in range(15):
            d.observe("ctx", "unit", "old")
        for _ in range(19):  # corrections flood the rolling window
            d.observe("ctx", "unit", "new")
        self.assertEqual(d.default("ctx", "unit")["value"], "new")


class TestWhisper(unittest.TestCase):
    def _w(self):
        w = Whisperer()
        for amt in [1000, 1100, 950, 1050, 1000, 1020, 980]:
            w.observe("T-MALARIA", amt)
        return w

    def test_no_baseline_no_opinion(self):
        w = Whisperer()
        w.observe("X", 100)
        self.assertEqual(w.check("X", 99999)["state"], "no-baseline")

    def test_within_norm_is_quiet(self):
        self.assertEqual(self._w().check("T-MALARIA", 1030)["state"],
                         "within-norm")

    def test_outlier_whispers_advisory_never_blocks(self):
        r = self._w().check("T-MALARIA", 50000)
        self.assertEqual(r["state"], "whisper")
        self.assertEqual(r["severity"], "advisory")
        self.assertIn("usual", r["message"])

    def test_outlier_history_does_not_poison_baseline(self):
        w = self._w()
        w.observe("T-MALARIA", 90000)  # one rogue confirmed claim
        # baseline is median+MAD: still ~1000, next normal value is quiet
        self.assertEqual(w.check("T-MALARIA", 1040)["state"], "within-norm")


class TestRestock(unittest.TestCase):
    def test_draft_shows_its_working(self):
        cons = {"amoxicillin": [(i * DAY, 10) for i in range(30)]}  # 10/day
        r = restock.draft({"amoxicillin": 50}, cons)
        line = r["lines"][0]
        # 300 units over a 29-day span = 10.345/day; target 310.3 - 50 = 260.3 -> 270
        self.assertEqual(line["suggested_qty"], 270)
        self.assertAlmostEqual(line["derivation"]["daily_velocity"], 300 / 29, places=2)
        self.assertEqual(r["state"], "draft")

    def test_healthy_stock_is_silent(self):
        cons = {"x": [(i * DAY, 5) for i in range(30)]}
        r = restock.draft({"x": 500}, cons)  # 100 days of cover
        self.assertEqual(r["lines"], [])

    def test_no_history_is_flagged_not_guessed(self):
        r = restock.draft({"new-item": 3}, {})
        self.assertEqual(r["no_velocity"], ["new-item"])
        self.assertEqual(r["lines"], [])

    def test_negative_stock_fails_closed(self):
        r = restock.draft({"bad": -1}, {})
        self.assertEqual(r["exceptions"][0]["reason"],
                         "negative-stock-data-error")

    def test_velocity_needs_real_span(self):
        self.assertIsNone(restock.velocity([(0, 5)]))
        self.assertIsNone(restock.velocity([(0, 5), (DAY, 5)]))  # 1-day span


if __name__ == "__main__":
    unittest.main()
