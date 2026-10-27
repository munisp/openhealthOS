#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from mpdsr import DeathRegistry, MPDSRError, age_band  # noqa: E402

class TestBands(unittest.TestCase):
    def test_bands(self):
        self.assertEqual(age_band(None, stillbirth=True), "stillbirth")
        self.assertEqual(age_band(12), "neonatal")
        self.assertEqual(age_band(200), "infant")
        self.assertEqual(age_band(900), "child-1-59m")
        with self.assertRaises(MPDSRError):
            age_band(2000)
        with self.assertRaises(MPDSRError):
            age_band(None)

class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.r = DeathRegistry()
        self.r.notify("D-1", age_days=3, place="facility", facility="PHC-007",
                      death_day="2026-10-20", notified_day="2026-10-20",
                      reporter="nurse-a")
        self.r.notify("D-2", age_days=400, place="home", facility="PHC-007",
                      death_day="2026-10-01", notified_day="2026-10-15",
                      reporter="chw-b")

    def test_late_flagged_not_refused(self):
        self.assertFalse(self.r.deaths["D-1"]["late_notification"])
        self.assertTrue(self.r.deaths["D-2"]["late_notification"])

    def test_review_requires_note_and_argument(self):
        with self.assertRaises(MPDSRError):
            self.r.review("D-1", reviewer="dr-x", factors=["none-argued"],
                          delays=[], preventable=False, note=" ",
                          day="2026-10-21")
        with self.assertRaises(MPDSRError):
            self.r.review("D-1", reviewer="dr-x", factors=[],
                          delays=[], preventable=False, note="natural",
                          day="2026-10-21")

    def test_preventable_must_name_factors(self):
        with self.assertRaises(MPDSRError):
            self.r.review("D-1", reviewer="dr-x", factors=[], delays=[],
                          preventable=True, note="was preventable",
                          day="2026-10-21")

    def test_review_and_actions(self):
        self.r.review("D-1", reviewer="dr-x",
                      factors=["stockout-blood-or-oxygen"],
                      delays=["receiving-care"], preventable=True,
                      note="no oxygen at facility",
                      actions=[{"action": "procure oxygen concentrator",
                                "owner": "lga-pharm", "due_day": "2026-10-25"}],
                      day="2026-10-21")
        s = self.r.summary("2026-10-26")
        self.assertEqual(s["preventable_reviewed"], 1)
        self.assertEqual(len(s["overdue_actions"]), 1)
        self.r.complete_action("D-1", 0, "2026-10-24")
        self.assertEqual(self.r.summary("2026-10-26")["overdue_actions"], [])

    def test_unreviewed_is_a_finding(self):
        s = self.r.summary("2026-10-21")
        self.assertTrue(s["unreviewed_is_a_finding"])
        self.assertEqual(s["unreviewed"], ["D-1", "D-2"])
        self.assertEqual(s["late_notifications"], ["D-2"])

    def test_no_rewrite_only_addenda(self):
        self.r.review("D-1", reviewer="dr-x", factors=["none-argued"],
                      delays=[], preventable=False, note="argued: SIDS",
                      day="2026-10-21")
        with self.assertRaises(MPDSRError):
            self.r.review("D-1", reviewer="dr-y", factors=[],
                          delays=[], preventable=False, note="x",
                          day="2026-10-22")
        self.r.addendum("D-1", "lab result arrived post-review",
                        "2026-10-22", by="dr-x")
        self.assertEqual(len(self.r.deaths["D-1"]["review"]["addenda"]), 1)

    def test_unknown_factor_delay_and_duplicates_refused(self):
        with self.assertRaises(MPDSRError):
            self.r.review("D-1", reviewer="dr", factors=["aliens"],
                          delays=[], preventable=True, note="x",
                          day="2026-10-21")
        with self.assertRaises(MPDSRError):
            self.r.notify("D-1", age_days=3, place="f", facility="f",
                          death_day="2026-10-20", notified_day="2026-10-20",
                          reporter="x")

if __name__ == "__main__":
    unittest.main()
