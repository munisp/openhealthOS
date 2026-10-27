#!/usr/bin/env python3
import os, sys, unittest
sys.path.insert(0, os.path.dirname(__file__))
from riskcomm import Campaign, RiskCommError  # noqa: E402

class TestRiskComm(unittest.TestCase):
    def setUp(self):
        self.c = Campaign("ebola-fct-oct26", "msg-v1",
                          {"en": "Report fever immediately. Call 6232.",
                           "ha": "A kai rahoto zazzabi nan take."})
        self.c.target("C-01", "en")
        self.c.target("C-02", "yo")  # not rendered -> flagged fallback
        self.c.target("C-03", "ha")

    def test_language_fallback_flagged(self):
        m = self.c.message_for("C-02")
        self.assertEqual(m["language"], "en")
        self.assertTrue(m["language_flagged"])
        self.assertEqual(self.c.message_for("C-03")["language"], "ha")

    def test_only_ack_counts(self):
        self.c.mark_delivered("C-01")
        self.c.acknowledge("C-01")
        self.c.mark_delivered("C-02")
        r = self.c.report()
        self.assertEqual(r["acknowledged"], 1)
        self.assertIn("C-02", r["unacknowledged_followup_list"])
        self.assertIn("C-03", r["unacknowledged_followup_list"])
        self.assertIn("delivered means", r["honest_note"])

    def test_state_machine_enforced(self):
        self.c.mark_delivered("C-01")            # queued -> delivered
        with self.assertRaises(RiskCommError):   # no double delivery
            self.c.mark_delivered("C-01")
        self.c.acknowledge("C-01")
        with self.assertRaises(RiskCommError):   # acked is terminal
            self.c.acknowledge("C-01")

    def test_constraints(self):
        with self.assertRaises(RiskCommError):
            Campaign("x", "v", {"fr": "bonjour"})
        with self.assertRaises(RiskCommError):
            self.c.target("C-04", "fr")
        with self.assertRaises(RiskCommError):
            self.c.target("C-01", "en")

if __name__ == "__main__":
    unittest.main()
