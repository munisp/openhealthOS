"""test_sepsis.py — every hour accountable, every waiver explicit,
every violation on the record."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from sepsis import SepsisClock, SepsisError, SEPSIS_BUNDLE_VERSION  # noqa: E402


def clock():
    return SepsisClock()


class TestClockStart(unittest.TestCase):
    def test_zero_requires_source_and_signature(self):
        s = clock()
        with self.assertRaises(SepsisError):
            s.start_clock("p1", zero_hour=0, by="mo-1",
                          source="vibes")
        with self.assertRaises(SepsisError):
            s.start_clock("p1", zero_hour=0, by="",
                          source="news2-escalation")

    def test_second_clock_refuses(self):
        s = clock()
        s.start_clock("p1", zero_hour=0, by="mo-1",
                      source="clinical-gestalt")
        with self.assertRaises(SepsisError):
            s.start_clock("p1", zero_hour=5, by="mo-1",
                          source="qsofa")


class TestElements(unittest.TestCase):
    def setUp(self):
        self.s = clock()
        self.s.start_clock("p1", zero_hour=0, by="mo-1",
                           source="news2-escalation")

    def test_unknown_element_and_duplicates_refuse(self):
        with self.assertRaises(SepsisError):
            self.s.complete("p1", "prayers", hour=0, by="mo-1")
        self.s.complete("p1", "lactate", hour=0, by="mo-1")
        with self.assertRaises(SepsisError):
            self.s.complete("p1", "lactate", hour=1, by="mo-1")

    def test_met_and_breached_late(self):
        on_time = self.s.complete("p1", "lactate", hour=1, by="mo-1")
        self.assertTrue(on_time["met"])
        late = self.s.complete("p1", "blood-cultures", hour=3,
                               by="mo-1")
        self.assertFalse(late["met"])
        self.assertEqual(late["elapsed_hours"], 3)

    def test_cultures_before_antibiotics_flagged_not_erased(self):
        out = self.s.complete("p1", "broad-spectrum-antibiotics",
                              hour=0, by="mo-1")
        self.assertEqual(out["sequence_violation"], ["blood-cultures"])
        self.s.complete("p1", "blood-cultures", hour=1, by="mo-1")
        rep = self.s.report(as_of=6)
        self.assertEqual(rep["sequence_violations"], 1)

    def test_before_time_zero_refuses(self):
        with self.assertRaises(SepsisError):
            self.s.complete("p1", "lactate", hour=-1, by="mo-1")


class TestWaivers(unittest.TestCase):
    def setUp(self):
        self.s = clock()
        self.s.start_clock("p1", zero_hour=0, by="mo-1",
                           source="clinical-gestalt")

    def test_core_elements_never_waive(self):
        for el in ("lactate", "blood-cultures",
                   "broad-spectrum-antibiotics"):
            with self.assertRaises(SepsisError):
                self.s.waive("p1", el, hour=0, by="mo-1",
                             reason="not indicated")

    def test_conditional_waiver_needs_reason(self):
        with self.assertRaises(SepsisError):
            self.s.waive("p1", "vasopressors", hour=1, by="mo-1",
                         reason="")
        out = self.s.waive("p1", "vasopressors", hour=1, by="mo-1",
                           reason="MAP 72 after fluids")
        self.assertEqual(out["reason"], "MAP 72 after fluids")
        with self.assertRaises(SepsisError):   # no double-waiver
            self.s.waive("p1", "vasopressors", hour=2, by="mo-1",
                         reason="still fine")
        with self.assertRaises(SepsisError):   # waived ≠ completed
            self.s.complete("p1", "vasopressors", hour=2, by="mo-1")


class TestBreachesAndReport(unittest.TestCase):
    def test_breaches_overdue_first(self):
        s = clock()
        s.start_clock("p-neglected", zero_hour=0, by="mo-1",
                      source="qsofa")
        s.start_clock("p-managed", zero_hour=0, by="mo-1",
                      source="qsofa")
        s.complete("p-managed", "lactate", hour=0, by="mo-1")
        s.complete("p-managed", "blood-cultures", hour=0, by="mo-1")
        s.complete("p-managed", "broad-spectrum-antibiotics", hour=1,
                   by="mo-1")
        s.complete("p-managed", "fluids-30mlkg", hour=2, by="mo-1")
        s.waive("p-managed", "vasopressors", hour=2, by="mo-1",
                reason="MAP 70 after fluids")
        s.complete("p-managed", "repeat-lactate", hour=5, by="mo-1")
        br = s.breaches(as_of=8)
        self.assertTrue(br)
        self.assertTrue(all(b["patient_id"] == "p-neglected"
                            for b in br))
        self.assertEqual(br[0]["element"], "lactate")  # most overdue
        self.assertEqual(br[0]["overdue_hours"], 7)

    def test_report_all_or_nothing_with_denominators(self):
        s = clock()
        s.start_clock("p-good", zero_hour=0, by="mo-1", source="qsofa")
        for el, h in (("lactate", 0), ("blood-cultures", 0),
                      ("broad-spectrum-antibiotics", 1),
                      ("fluids-30mlkg", 2), ("repeat-lactate", 5)):
            s.complete("p-good", el, hour=h, by="mo-1")
        s.waive("p-good", "vasopressors", hour=2, by="mo-1",
                reason="normotensive")
        s.start_clock("p-bad", zero_hour=0, by="mo-1", source="qsofa")
        s.complete("p-bad", "lactate", hour=4, by="mo-1")   # late
        s.complete("p-bad", "blood-cultures", hour=4, by="mo-1")
        s.complete("p-bad", "broad-spectrum-antibiotics", hour=5,
                   by="mo-1")
        s.complete("p-bad", "fluids-30mlkg", hour=5, by="mo-1")
        s.waive("p-bad", "vasopressors", hour=5, by="mo-1",
                reason="normotensive")
        s.complete("p-bad", "repeat-lactate", hour=6, by="mo-1")
        rep = s.report(as_of=6)
        self.assertEqual(rep["clocks"], 2)
        self.assertEqual(rep["all_or_nothing"], {"n": 2, "met": 1,
                                                 "rate": 0.5})
        self.assertEqual(rep["per_element"]["lactate"]["met"], 1)
        self.assertEqual(rep["per_element"]["lactate"]["applicable"], 2)
        self.assertEqual(rep["median_hours_to_antibiotics"], 3.0)
        self.assertEqual(rep["bundle_version"], SEPSIS_BUNDLE_VERSION)


if __name__ == "__main__":
    unittest.main()
