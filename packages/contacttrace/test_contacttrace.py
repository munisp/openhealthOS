#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from contacttrace import ContactGraph, TraceError, export_worklist_lines, ingest_checkin_lines  # noqa: E402


def seeded():
    g = ContactGraph()
    g.add_case("CASE-1", "2026-10-20")
    g.add_contact("CASE-1", "C-01", "2026-10-07", tracer="t-a")
    g.add_contact("CASE-1", "C-02", "2026-10-07", tracer="t-a")
    g.add_contact("CASE-1", "C-03", "2026-10-08", tracer="t-b")
    return g


class TestGraph(unittest.TestCase):
    def test_contact_lifecycle_to_cleared(self):
        g = seeded()
        for d in range(8, 28):  # check in daily from exposure+1
            g.checkin("C-01", f"2026-10-{d:02d}")
        out = g.sweep("2026-10-28")  # day 21
        self.assertIn("C-01", out["cleared"])
        self.assertEqual(g.contacts["C-01"]["state"], "cleared")

    def test_symptomatic_checkin_converts_one_way(self):
        g = seeded()
        out = g.checkin("C-02", "2026-10-10", symptomatic=True)
        self.assertEqual(out["action"], "create-case-and-start-ring")
        with self.assertRaises(TraceError):
            g.checkin("C-02", "2026-10-11")  # converted: check-ins closed
        with self.assertRaises(TraceError):
            g._set(g.contacts["C-02"], "monitoring")

    def test_missed_checkin_marks_overdue_then_recovers(self):
        g = seeded()
        g.checkin("C-03", "2026-10-09")
        out = g.sweep("2026-10-11")  # missed the 10th
        self.assertIn("C-03", out["overdue"])
        g.checkin("C-03", "2026-10-11")
        self.assertEqual(g.contacts["C-03"]["state"], "monitoring")

    def test_generation_counts_through_conversion_chain(self):
        g = seeded()
        g.checkin("C-01", "2026-10-12", symptomatic=True)
        g.add_case("CASE-2", "2026-10-12", source_contact_id="C-01")
        rec = g.add_contact("CASE-2", "C-10", "2026-10-12", tracer="t-a")
        self.assertEqual(rec["generation"], 2)
        self.assertEqual(g.stats()["max_generation"], 2)

    def test_reexposure_records_new_link_not_overwrite(self):
        g = seeded()
        g.add_case("CASE-2", "2026-10-15")
        g.add_contact("CASE-2", "C-01", "2026-10-15")
        self.assertEqual(g.contacts["C-01"]["exposure_day"], "2026-10-07")
        self.assertEqual(len(g.contacts["C-01"]["reexposures"]), 1)

    def test_lost_requires_reason(self):
        g = seeded()
        with self.assertRaises(TraceError):
            g.mark_lost("C-03", "  ")
        g.mark_lost("C-03", "relocated, untraceable after 3 visits")
        self.assertEqual(g.contacts["C-03"]["state"], "lost")

    def test_worklist_prioritises_overdue(self):
        g = seeded()
        g.checkin("C-01", "2026-10-09")
        wl = g.worklist("t-a", "2026-10-11")
        self.assertEqual(wl["overdue"], ["C-01", "C-02"])  # C-02 never checked in
        self.assertIn("t-a", wl["tracer"])

    def test_duplicate_case_and_unknown_refs_refused(self):
        g = seeded()
        with self.assertRaises(TraceError):
            g.add_case("CASE-1", "2026-10-20")
        with self.assertRaises(TraceError):
            g.add_contact("CASE-99", "C-09", "2026-10-20")
        with self.assertRaises(TraceError):
            g.checkin("C-99", "2026-10-20")

    def test_stats_and_completeness(self):
        g = seeded()
        g.checkin("C-01", "2026-10-09")
        g.sweep("2026-10-11")
        s = g.stats()
        self.assertEqual(s["cases"], 1)
        self.assertEqual(s["contacts"], 3)
        self.assertLess(s["followup_completeness"], 1.0)


if __name__ == "__main__":
    unittest.main()


class TestChatSeam(unittest.TestCase):
    def test_worklist_export_and_checkin_ingest_roundtrip(self):
        import json
        g = seeded()
        g.checkin("C-01", "2026-10-09")
        lines = export_worklist_lines(g, "2026-10-11")
        wl = json.loads(lines[0])
        self.assertEqual(wl["kind"], "trace-worklist")
        self.assertIn("C-01", wl["overdue"])

        evs = [json.dumps({"kind": "trace-checkin", "contact": "C-01",
                           "day": "2026-10-11", "symptomatic": False}),
               json.dumps({"kind": "trace-checkin", "contact": "C-03",
                           "day": "2026-10-11", "symptomatic": True}),
               "{broken json",
               json.dumps({"kind": "trace-checkin", "contact": "C-99",
                           "day": "2026-10-11"})]
        out = ingest_checkin_lines(g, evs)
        self.assertEqual(out["applied"], 2)
        self.assertEqual(len(out["rejected"]), 2)
        self.assertEqual(g.contacts["C-01"]["state"], "monitoring")
        self.assertEqual(g.contacts["C-03"]["state"], "converted")
