"""test_medrec.py — provenance or it didn't happen; every drug earns
a decision; discrepancies are computed, not asserted."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from medrec import MedRec, MedRecError, MEDREC_VERSION  # noqa: E402


class TestStartAndProvenance(unittest.TestCase):
    def setUp(self):
        self.m = MedRec()
        self.m.start("p1", setting="admission", day=0, by="clerk-1")

    def test_second_start_refuses(self):
        with self.assertRaises(MedRecError):
            self.m.start("p1", setting="admission", day=1,
                         by="clerk-2")
        self.m.start("p1", setting="discharge", day=5, by="clerk-1")

    def test_home_med_needs_provenance(self):
        with self.assertRaises(MedRecError):
            self.m.record_home_med("p1", "metformin",
                                   setting="admission", dose="500 bd",
                                   source="vibes", day=0)
        with self.assertRaises(MedRecError):
            self.m.record_home_med("p1", "metformin",
                                   setting="admission", dose="",
                                   source="patient-report", day=0)
        out = self.m.record_home_med("p1", "metformin",
                                     setting="admission",
                                     dose="500 bd",
                                     source="brought-in-medications",
                                     day=0)
        self.assertEqual(out["source"], "brought-in-medications")
        with self.assertRaises(MedRecError):   # no overwrite
            self.m.record_home_med("p1", "metformin",
                                   setting="admission", dose="1g bd",
                                   source="gp-letter", day=1)


class TestDecisions(unittest.TestCase):
    def setUp(self):
        self.m = MedRec()
        self.m.start("p1", setting="admission", day=0, by="clerk-1")
        self.m.record_home_med("p1", "metformin", setting="admission",
                               dose="500 bd", source="gp-letter",
                               day=0)
        self.m.record_home_med("p1", "amlodipine", setting="admission",
                               dose="5 od", source="patient-report",
                               day=0)

    def test_decision_rules(self):
        with self.assertRaises(MedRecError):   # unknown drug
            self.m.reconcile("p1", "warfarin", setting="admission",
                             decision="stop", day=0, by="mo-1",
                             reason="x")
        with self.assertRaises(MedRecError):   # stop needs reason
            self.m.reconcile("p1", "metformin", setting="admission",
                             decision="stop", day=0, by="mo-1")
        with self.assertRaises(MedRecError):   # substitute needs what
            self.m.reconcile("p1", "amlodipine", setting="admission",
                             decision="substitute", day=0, by="mo-1")
        out = self.m.reconcile("p1", "metformin", setting="admission",
                               decision="withhold", day=0, by="mo-1",
                               reason="NBM pre-op; restart with diet")
        self.assertEqual(out["reason"], "NBM pre-op; restart with diet")
        with self.assertRaises(MedRecError):   # no rewrite
            self.m.reconcile("p1", "metformin", setting="admission",
                             decision="continue", day=1, by="mo-1")

    def test_discrepancies_computed_both_ways(self):
        self.m.reconcile("p1", "metformin", setting="admission",
                         decision="stop", day=0, by="mo-1",
                         reason="AKI on admission")
        self.m.note_order("p1", "metformin", setting="admission",
                          day=1)   # someone ordered it anyway — loud
        disc = self.m.discrepancies("p1")
        kinds = {d["drug"]: d["kind"] for d in disc}
        self.assertEqual(kinds["amlodipine"], "undecided")
        self.assertEqual(kinds["metformin"], "ordered-despite-stop")


class TestWorklists(unittest.TestCase):
    def test_unreconciled_overdue_first(self):
        m = MedRec()
        m.start("p-old", setting="admission", day=0, by="c")
        m.record_home_med("p-old", "aspirin", setting="admission",
                          dose="75 od", source="gp-letter", day=0)
        m.start("p-new", setting="admission", day=5, by="c")
        m.record_home_med("p-new", "aspirin", setting="admission",
                          dose="75 od", source="patient-report", day=5)
        m.start("p-done", setting="admission", day=0, by="c")
        m.record_home_med("p-done", "aspirin", setting="admission",
                          dose="75 od", source="gp-letter", day=0)
        m.reconcile("p-done", "aspirin", setting="admission",
                    decision="continue", day=0, by="mo-1")
        out = m.unreconciled(as_of=10)
        self.assertEqual([o["patient_id"] for o in out],
                         ["p-old", "p-new"])

    def test_report_shows_denominators(self):
        m = MedRec()
        m.start("p1", setting="admission", day=0, by="c")
        for drug in ("aspirin", "metformin"):
            m.record_home_med("p1", drug, setting="admission",
                              dose="x", source="gp-letter", day=0)
        m.reconcile("p1", "aspirin", setting="admission",
                    decision="continue", day=0, by="mo-1")
        rep = m.report()
        adm = rep["per_setting"]["admission"]
        self.assertEqual(adm["meds"], 2)
        self.assertEqual(adm["decided"], 1)
        self.assertEqual(adm["decision_rate"], 0.5)
        self.assertEqual(rep["medrec_version"], MEDREC_VERSION)


if __name__ == "__main__":
    unittest.main()
