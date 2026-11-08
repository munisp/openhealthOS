"""Tests for packages/nursing — flowsheets, I&O, SBAR handoff."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from nursing import (  # noqa: E402
    Flowsheet, FluidBalance, Handoff, WardReport, Error, VERSION,
)


class TestFlowsheet(unittest.TestCase):
    def setUp(self):
        self.fs = Flowsheet("Pax-1", "ADM-1")

    def test_record_and_series(self):
        self.fs.record("heart-rate", 82, day=1, minute=480, by="nurse-ada")
        self.fs.record("heart-rate", 88, day=1, minute=720, by="nurse-ada")
        self.assertEqual(len(self.fs.series("heart-rate")), 2)
        self.assertEqual(self.fs.latest("heart-rate")["value"], 88)

    def test_implausible_refused(self):
        with self.assertRaises(Error):
            self.fs.record("heart-rate", 400, day=1, minute=480,
                           by="nurse-ada")

    def test_unknown_vital_refused(self):
        with self.assertRaises(Error):
            self.fs.record("mood", 5, day=1, minute=480, by="nurse-ada")

    def test_unnamed_recorder_refused(self):
        with self.assertRaises(Error):
            self.fs.record("spo2", 98, day=1, minute=480, by="")

    def test_correction_cites_original(self):
        row = self.fs.record("temperature", 39.9, day=1, minute=480,
                             by="nurse-ada")
        with self.assertRaises(Error):
            self.fs.record("temperature", 37.9, day=1, minute=485,
                           by="nurse-ada", amends="NOPE-1")
        fix = self.fs.record("temperature", 37.9, day=1, minute=485,
                             by="nurse-ada", amends=row["row_id"])
        self.assertEqual(fix["amends"], row["row_id"])
        # original stands — append-only
        self.assertEqual(len(self.fs.series("temperature")), 2)

    def test_overdue(self):
        self.fs.record("spo2", 97, day=1, minute=100, by="nurse-ada")
        self.assertEqual(self.fs.overdue("spo2", day=1, minute=100,
                                         max_gap_minutes=60), 0)
        self.assertEqual(self.fs.overdue("spo2", day=1, minute=500,
                                         max_gap_minutes=60), 400)
        self.assertIsNone(self.fs.overdue("heart-rate", day=1, minute=500,
                                          max_gap_minutes=60))


class TestFluidBalance(unittest.TestCase):
    def setUp(self):
        self.fb = FluidBalance("Pax-1", "ADM-1")

    def test_totals_and_balance(self):
        self.fb.record("intake", "oral", 500, day=1, minute=480,
                       by="nurse-ada")
        self.fb.record("intake", "iv", 1000, day=1, minute=500,
                       by="nurse-ada")
        self.fb.record("output", "urine", 400, day=1, minute=520,
                       by="nurse-ada")
        t = self.fb.totals(1)
        self.assertEqual(t["intake_ml"], 1500)
        self.assertEqual(t["output_ml"], 400)
        self.assertEqual(t["balance_ml"], 1100)
        self.assertFalse(t["negative_balance"])

    def test_negative_balance_flagged_not_clamped(self):
        self.fb.record("output", "urine", 900, day=1, minute=480,
                       by="nurse-ada")
        t = self.fb.totals(1)
        self.assertEqual(t["balance_ml"], -900)
        self.assertTrue(t["negative_balance"])

    def test_wrong_kind_refused(self):
        with self.assertRaises(Error):
            self.fb.record("intake", "urine", 200, day=1, minute=480,
                           by="nurse-ada")

    def test_implausible_volume_refused(self):
        with self.assertRaises(Error):
            self.fb.record("intake", "iv", 9000, day=1, minute=480,
                           by="nurse-ada")

    def test_running_balance(self):
        self.fb.record("intake", "iv", 500, day=1, minute=100,
                       by="nurse-ada")
        self.fb.record("output", "vomit", 200, day=1, minute=200,
                       by="nurse-ada")
        self.assertEqual(self.fb.running_balance(day=1, minute=150), 500)
        self.assertEqual(self.fb.running_balance(day=1, minute=300), 300)


class TestHandoff(unittest.TestCase):
    def _make(self):
        return Handoff("HO-1", "Pax-1", day=1, shift="morning",
                       from_nurse="nurse-ada",
                       situation="post-op day 1, stable",
                       background="appendectomy yesterday",
                       assessment="vitals stable, tolerating orals",
                       recommendation="continue obs q4h")

    def test_two_signatures(self):
        h = self._make()
        self.assertFalse(h.countersigned)
        h.countersign(by="nurse-bola")
        self.assertTrue(h.countersigned)
        with self.assertRaises(Error):
            h.countersign(by="nurse-bola")

    def test_same_hand_refused(self):
        h = self._make()
        with self.assertRaises(Error):
            h.countersign(by="nurse-ada")

    def test_empty_sbar_refused(self):
        with self.assertRaises(Error):
            Handoff("HO-2", "Pax-1", day=1, shift="night",
                    from_nurse="nurse-ada", situation="",
                    background="b", assessment="a", recommendation="r")

    def test_unknown_shift_refused(self):
        with self.assertRaises(Error):
            Handoff("HO-3", "Pax-1", day=1, shift=" brunch ",
                    from_nurse="nurse-ada", situation="s", background="b",
                    assessment="a", recommendation="r")


class TestWardReport(unittest.TestCase):
    def test_report_surfaces_gaps(self):
        fs = Flowsheet("Pax-1", "ADM-1")
        fb = FluidBalance("Pax-1", "ADM-1")
        fb.record("intake", "iv", 3000, day=1, minute=100, by="nurse-ada")
        fb.record("output", "urine", 200, day=1, minute=200, by="nurse-ada")
        ward = WardReport("ward-a")
        ward.add_patient(fs, fb)
        ward.add_handoff(Handoff("HO-1", "Pax-1", day=1, shift="morning",
                                 from_nurse="nurse-ada", situation="s",
                                 background="b", assessment="a",
                                 recommendation="r"))
        rep = ward.report(day=1, minute=700)
        self.assertEqual(len(rep["overdue_vitals"]), 1)
        self.assertEqual(len(rep["fluid_flags"]), 1)
        self.assertEqual(len(rep["unsigned_handoffs"]), 1)
        self.assertEqual(len(rep["sha256"]), 64)
        self.assertEqual(rep["version"], VERSION)


if __name__ == "__main__":
    unittest.main()
