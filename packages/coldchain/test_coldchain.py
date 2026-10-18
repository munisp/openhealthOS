#!/usr/bin/env python3
"""Cold chain: excursions are P1, drift predicts the breach, stable is quiet."""
import unittest

from coldchain import Reading, analyze


def series(temps, start_hour=0):
    return [Reading(f"2026-10-18T{start_hour + i:02d}:00:00Z", t) for i, t in enumerate(temps)]


class TestColdChain(unittest.TestCase):
    def test_excursion_is_p1_and_dangerous(self):
        t = analyze("FR-1", series([4.0, 4.5, 6.0, 9.2]))
        self.assertEqual(t.priority, "P1")
        self.assertTrue(t.to_event()["danger"])
        self.assertIn("EXCURSION", t.reason)

    def test_warming_drift_predicts_breach(self):
        # +0.5°C/h from 5°C → crosses 8°C in 6h
        t = analyze("FR-2", series([3.5, 4.0, 4.5, 5.0, 5.5]))
        self.assertEqual(t.priority, "P2")
        self.assertIn("DRIFT", t.reason)
        self.assertTrue(t.predicted_breach_ts)
        self.assertFalse(t.to_event()["danger"])

    def test_cooling_drift_predicts_freeze(self):
        t = analyze("FR-3", series([5.5, 5.0, 4.5, 4.0, 3.5]))
        self.assertEqual(t.priority, "P2")
        self.assertIn("2°C", t.reason)

    def test_stable_fridge_is_quiet(self):
        self.assertIsNone(analyze("FR-4", series([4.9, 5.0, 5.1, 5.0, 4.9])))

    def test_too_few_readings_abstains(self):
        self.assertIsNone(analyze("FR-5", series([9.0, 9.5])))


if __name__ == "__main__":
    unittest.main()
