#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from ledger import LedgerError, SignalLedger  # noqa: E402

class TestLedger(unittest.TestCase):
    def setUp(self):
        self.l = SignalLedger(resolution_days=14)
        self.l.register("SIG-1", "scout", "2026-10-10", 2.0)
        self.l.register("SIG-2", "ebs", "2026-10-11", 2.0)
        self.l.register("SIG-3", "scout", "2026-10-12", 2.0)

    def test_label_and_precision(self):
        self.l.label("SIG-1", "verified-case", "2026-10-13", "confirmed by PCR")
        self.l.label("SIG-2", "false-alarm", "2026-10-13", "team: unfounded")
        self.l.label("SIG-3", "false-alarm", "2026-10-14", "food poisoning")
        r = self.l.precision_report("2026-10-14")
        self.assertEqual(r["by_source"]["scout"]["precision"], 0.5)
        self.assertEqual(r["unresolved_count"], 0)
        self.assertEqual(r["warning"], "")

    def test_unresolved_reported_loudly(self):
        self.l.label("SIG-1", "false-alarm", "2026-10-13", "x")
        r = self.l.precision_report("2026-10-30")
        self.assertEqual(r["unresolved_count"], 2)
        self.assertIn("SIG-2", r["unresolved_ids"])
        self.assertIn("resolved subset only", r["warning"])

    def test_relabel_refused_correction_appended(self):
        self.l.label("SIG-1", "false-alarm", "2026-10-13", "initial")
        with self.assertRaises(LedgerError):
            self.l.label("SIG-1", "verified-case", "2026-10-14", "redo")
        self.l.correct("SIG-1", "verified-case", "2026-10-14",
                       "repeat PCR positive")
        self.assertEqual(self.l.signals["SIG-1"]["outcome"], "verified-case")
        self.assertEqual(len(self.l.signals["SIG-1"]["history"]), 2)

    def test_note_and_outcome_validated(self):
        with self.assertRaises(LedgerError):
            self.l.label("SIG-1", "verified-case", "2026-10-13", " ")
        with self.assertRaises(LedgerError):
            self.l.label("SIG-1", "whatever", "2026-10-13", "x")

    def test_policyeval_export_refuses_to_invent_propensity(self):
        self.l.label("SIG-1", "verified-case", "2026-10-13", "pcr")
        out = self.l.to_policyeval_events(2.0)
        self.assertEqual(out[0]["reward"], 1.0)
        self.assertIsNone(out[0]["propensity"])

if __name__ == "__main__":
    unittest.main()
