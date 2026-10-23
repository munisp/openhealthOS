#!/usr/bin/env python3
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dhis2"))
from bridge import aggregate_reported, emit  # noqa: E402
from hardened import ResendGuard, replay_spool  # noqa: E402

EVENTS = [
    {"type": "encounter", "facility": "PHC-007", "ts": "2026-10-01T09:00:00Z"},
    {"type": "immunisation", "facility": "PHC-007", "ts": "2026-10-02T11:30:00Z"},
    {"type": "unknown_thing", "facility": "PHC-007", "ts": "2026-10-01T09:00:00Z"},
    {"type": "encounter", "ts": "2026-10-01T09:00:00Z"},          # no facility
    {"type": "dispensing", "facility": "PHC-007"},                # no ts
    {"facility": "PHC-007", "ts": "2026-10-01T09:00:00Z"},        # no type
]


class TestAggregateReported(unittest.TestCase):
    def test_nothing_drops_silently(self):
        values, drops = aggregate_reported(EVENTS)
        self.assertEqual(len(values), 2)  # encounter + immunisation
        self.assertEqual(drops["unmapped_event_types"], {"unknown_thing": 1})
        self.assertEqual(drops["malformed_events"], 3)
        self.assertEqual(drops["received"], 6)

    def test_clean_stream_reports_no_drops(self):
        values, drops = aggregate_reported(EVENTS[:2])
        self.assertEqual(drops["unmapped_event_types"], {})
        self.assertEqual(drops["malformed_events"], 0)


class TestEmit(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.spool = os.path.join(self.dir, "spool.jsonl")
        self.guard = ResendGuard(os.path.join(self.dir, "manifest.json"))

    def test_emit_spools_with_integrity(self):
        out = emit(EVENTS[:2], spool_path=self.spool, guard=self.guard)
        self.assertEqual(out["resend"], "new")
        replayed = replay_spool(self.spool, lambda p: p)
        self.assertEqual(replayed["pushed"], 1)
        self.assertEqual(replayed["results"][0], out["payload"])

    def test_duplicate_emit_not_respooled(self):
        emit(EVENTS[:2], spool_path=self.spool, guard=self.guard)
        out = emit(EVENTS[:2], spool_path=self.spool, guard=self.guard)
        self.assertEqual(out["resend"], "duplicate")
        self.assertEqual(len(open(self.spool).read().splitlines()), 1)

    def test_changed_month_is_supersede_and_respooled(self):
        emit(EVENTS[:2], spool_path=self.spool, guard=self.guard)
        more = EVENTS[:2] + [EVENTS[0]]  # one more encounter
        out = emit(more, spool_path=self.spool, guard=self.guard)
        self.assertEqual(out["resend"], "supersede")
        self.assertEqual(len(open(self.spool).read().splitlines()), 2)

    def test_drops_ride_along(self):
        out = emit(EVENTS, spool_path=self.spool, guard=self.guard)
        self.assertEqual(out["drops"]["unmapped_event_types"],
                         {"unknown_thing": 1})


if __name__ == "__main__":
    unittest.main()
