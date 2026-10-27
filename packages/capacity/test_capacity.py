#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from capacity import CapacityBoard, CapacityError  # noqa: E402

class TestCapacity(unittest.TestCase):
    def setUp(self):
        self.b = CapacityBoard()
        self.b.attest("UATH", 20, 6, 4.0, 12, "2026-10-20", "admin-uath")
        self.b.attest("OLD-CTR", 10, 8, 5.0, 6, "2026-10-01", "admin-old")

    def test_query_separates_fresh_from_stale(self):
        q = self.b.query("2026-10-25")
        self.assertEqual(len(q["ready"]), 1)
        self.assertEqual(q["ready"][0]["centre"], "UATH")
        self.assertEqual(q["unverified_stale"][0]["centre"], "OLD-CTR")
        self.assertEqual(q["total_verified_beds"], 6)

    def test_stale_number_stays_visible_but_unverified(self):
        q = self.b.query("2026-10-25")
        self.assertEqual(q["unverified_stale"][0]["beds_free"], 8)
        self.assertFalse(q["unverified_stale"][0]["verified"])

    def test_where_requires_bed_ppe_and_staff(self):
        self.b.attest("NOPPE", 5, 5, 0.0, 8, "2026-10-24", "admin-x")
        out = self.b.where("2026-10-25", need_beds=1, min_ppe_days=1.0)
        self.assertTrue(out["ok"])
        self.assertEqual([o["centre"] for o in out["options"]], ["UATH"])

    def test_impossible_capacity_refused(self):
        with self.assertRaises(CapacityError):
            self.b.attest("X", 5, 9, 1.0, 2, "2026-10-25", "a")
        with self.assertRaises(CapacityError):
            self.b.attest("X", 5, 2, -1.0, 2, "2026-10-25", "a")
        with self.assertRaises(CapacityError):
            self.b.attest("X", 5, 2, 1.0, 2, "2026-10-25", " ")

    def test_no_options_is_honest(self):
        out = self.b.where("2026-10-25", need_beds=99)
        self.assertFalse(out["ok"])
        self.assertEqual(out["options"], [])
        self.assertEqual(out["stale_noted"], 1)

if __name__ == "__main__":
    unittest.main()
