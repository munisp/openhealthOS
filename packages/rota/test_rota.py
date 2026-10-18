#!/usr/bin/env python3
"""Rota tests: coverage gaps visible, no double-booking, weekly caps,
fair greedy autofill, fairness report."""
import unittest

from rota import Rota, RotaError, Shift, Staff


def build():
    r = Rota()
    r.add_staff(Staff("S1", "Amina", "nurse"))
    r.add_staff(Staff("S2", "Bala", "nurse"))
    r.add_staff(Staff("S3", "Chidi", "CHEW"))
    r.add_shift(Shift("D1", "2026-05-04", "day", {"nurse": 2, "CHEW": 1}))
    r.add_shift(Shift("N1", "2026-05-04", "night", {"nurse": 1}))
    r.add_shift(Shift("W1", "2026-05-09", "weekend-day", {"nurse": 1}))
    return r


class TestAssign(unittest.TestCase):
    def test_coverage_gap_visible(self):
        r = build()
        r.assign("D1", "S1")
        cov = {c["shift_id"]: c for c in r.coverage()}
        self.assertEqual(cov["D1"]["gaps"], {"nurse": 1, "CHEW": 1})
        self.assertFalse(cov["D1"]["ok"])
        self.assertEqual(cov["N1"]["gaps"], {"nurse": 1})

    def test_no_double_booking_same_window(self):
        r = build()
        r.add_shift(Shift("D2", "2026-05-04", "day", {"nurse": 1}))
        r.assign("D1", "S1")
        with self.assertRaises(RotaError):
            r.assign("D2", "S1")

    def test_weekly_cap(self):
        r = build()
        r.staff["S1"].max_shifts_per_week = 1
        r.assign("D1", "S1")
        with self.assertRaises(RotaError):
            r.assign("N1", "S1")

    def test_wrong_cadre_counts_nothing(self):
        r = build()
        r.assign("D1", "S3")  # CHEW fills the CHEW slot, not a nurse slot
        cov = {c["shift_id"]: c for c in r.coverage()}
        self.assertEqual(cov["D1"]["gaps"], {"nurse": 2})


class TestAutofill(unittest.TestCase):
    def test_fills_gaps_fairly(self):
        r = build()
        made = r.autofill()
        self.assertTrue(all(c["ok"] for c in r.coverage()))
        fair = r.fairness()
        # two nurses split the load — nobody carries everything
        self.assertGreater(fair["S1"]["total"], 0)
        self.assertGreater(fair["S2"]["total"], 0)
        self.assertEqual(len(made), 5)  # 2 nurses + 1 CHEW day, 1 night, 1 weekend

    def test_unfillable_gap_stays_visible(self):
        r = build()
        r.staff["S2"].max_shifts_per_week = 0  # effectively unavailable
        r.autofill()
        cov = {c["shift_id"]: c for c in r.coverage()}
        self.assertFalse(all(c["ok"] for c in cov.values()))

    def test_deterministic(self):
        a, b = build(), build()
        self.assertEqual(
            [(x.shift_id, x.staff_id) for x in a.autofill()],
            [(x.shift_id, x.staff_id) for x in b.autofill()])


if __name__ == "__main__":
    unittest.main()
