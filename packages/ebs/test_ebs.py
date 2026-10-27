#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from ebs import EBSError, SignalInbox  # noqa: E402


class TestEBS(unittest.TestCase):
    def setUp(self):
        self.inbox = SignalInbox()

    def test_full_lifecycle_to_verified_signal(self):
        r = self.inbox.intake("three sudden deaths after a burial in "
                              "kawo village with bleeding",
                              "community-call", "Kawo", "2026-10-21",
                              "community-leader")
        sid = r["id"]
        self.inbox.triage(sid, verifier="epi-fct", by="desk", day="2026-10-21")
        self.inbox.begin_verification(sid, "2026-10-21")
        out = self.inbox.resolve(sid, "verified",
                                 "team visited; deaths match suspect "
                                 "definition", "2026-10-22")
        self.assertIsNotNone(out["signal_draft"])
        self.assertEqual(out["signal_draft"]["requires"],
                         "human epidemiologist review")

    def test_rumour_is_never_an_alert_until_verified(self):
        r = self.inbox.intake("unusual fever cluster near the market",
                              "radio", "Karu", "2026-10-21", "dj")
        q = self.inbox.queue()
        self.assertIn(r["id"], q["inbox"])
        self.assertEqual(q["verified"], [])

    def test_dedupe_reports_corroboration(self):
        t = "deaths after funeral in same compound with high fever"
        a = self.inbox.intake(t, "radio", "Kawo", "2026-10-21", "dj")
        b = self.inbox.intake(t, "chw-report", "Kawo", "2026-10-21", "chw-7")
        self.assertIsNotNone(b["duplicate_of"])
        item = self.inbox.items[b["id"]]
        self.assertTrue(item["corroborates"])

    def test_verifier_and_note_required(self):
        r = self.inbox.intake("pharmacy selling out of ORS suddenly",
                              "pharmacy", "Gwagwa", "2026-10-21", "pharm-2")
        with self.assertRaises(EBSError):
            self.inbox.triage(r["id"], verifier=" ", by="desk",
                              day="2026-10-21")
        self.inbox.triage(r["id"], verifier="epi-fct", by="desk",
                          day="2026-10-21")
        with self.assertRaises(EBSError):
            self.inbox.resolve(r["id"], "dismissed", " ", "2026-10-21")

    def test_cannot_resolve_from_inbox_or_reresolve(self):
        r = self.inbox.intake("unexplained livestock deaths nearby",
                              "social-media", "Bwari", "2026-10-21", "mod")
        with self.assertRaises(EBSError):
            self.inbox.resolve(r["id"], "verified", "x", "2026-10-21")
        self.inbox.triage(r["id"], verifier="epi", by="desk",
                          day="2026-10-21")
        self.inbox.resolve(r["id"], "dismissed", "rumour traced, unfounded",
                           "2026-10-22")
        with self.assertRaises(EBSError):
            self.inbox.resolve(r["id"], "verified", "x", "2026-10-23")

    def test_unknown_source_and_empty_text_refused(self):
        with self.assertRaises(EBSError):
            self.inbox.intake("x", "whatsapp-forward", "Karu",
                              "2026-10-21", "anon")
        with self.assertRaises(EBSError):
            self.inbox.intake("  ", "radio", "Karu", "2026-10-21", "dj")


if __name__ == "__main__":
    unittest.main()
