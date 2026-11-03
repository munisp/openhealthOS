import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for pkg in ("teleread", "teleicu"):
    sys.path.insert(0, os.path.join(ROOT, pkg))

from teleread import ReaderRegistry, TeleReadError  # noqa: E402
from teleicu import TeleICU, FamilyLog, FAMILY_UPDATE_INTERVAL_DAYS  # noqa: E402


def make_icu():
    r = ReaderRegistry()
    r.register("icu-1", licence_id="I1", licence_expiry_day=400,
               scopes=("intensivist",))
    icu = TeleICU(r)
    icu.open_oversight("ICU-1", patient_id="p1", facility="f",
                       trigger_ref="TRG-1", day=0)
    icu.open_oversight("ICU-2", patient_id="p2", facility="f",
                       trigger_ref="TRG-2", day=0)
    return icu


class TestFamilyLog(unittest.TestCase):
    def setUp(self):
        self.icu = make_icu()
        self.log = FamilyLog(self.icu)

    def test_update_needs_channel_and_substance(self):
        with self.assertRaises(TeleReadError):
            self.log.record_update("ICU-1", day=1, by="icu-1",
                                   family_member="wife",
                                   channel="telepathy",
                                   summary="stable")
        with self.assertRaises(TeleReadError):
            self.log.record_update("ICU-1", day=1, by="icu-1",
                                   family_member="wife",
                                   channel="phone", summary="")

    def test_unknown_case_refuses(self):
        with self.assertRaises(Exception):
            self.log.record_update("ICU-GHOST", day=1, by="icu-1",
                                   family_member="wife", channel="phone",
                                   summary="stable")

    def test_silence_surfaces_overdue_first(self):
        self.log.record_update("ICU-1", day=1, by="icu-1",
                               family_member="wife", channel="in-person",
                               summary="stable overnight")
        due = self.log.updates_due(1 + FAMILY_UPDATE_INTERVAL_DAYS - 1)
        self.assertEqual([d["case_id"] for d in due], ["ICU-2"])
        self.assertTrue(due[0]["never_updated"])
        # at the boundary day ICU-1's silence also meets the interval;
        # longest silence first
        due2 = self.log.updates_due(10)
        self.assertEqual([d["case_id"] for d in due2],
                         ["ICU-2", "ICU-1"])

    def test_report_ships_denominator(self):
        self.log.record_update("ICU-1", day=1, by="icu-1",
                               family_member="brother", channel="video",
                               summary="extubated")
        rep = self.log.report(as_of=10)
        self.assertEqual(rep["open_cases"], 2)
        self.assertEqual(rep["updates_logged"], 1)
        self.assertEqual(rep["cases_silent_over_interval"], 2)
        self.assertEqual(rep["never_updated"], 1)


if __name__ == "__main__":
    unittest.main()
