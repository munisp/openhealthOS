#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from labtrack import LabTracker, LabTrackError  # noqa: E402


class TestLabTrack(unittest.TestCase):
    def setUp(self):
        self.t = LabTracker(whisper_margin_hours=6.0)

    def _complete(self, sid, lab, hours):
        self.t.register(sid, "CASE-1", lab, "2026-10-20T08:00:00Z")
        end_day = 20 + int((8 + hours) // 24)
        end_h = (8 + hours) % 24
        self.t.advance(sid, "in-transit", f"2026-10-20T09:00:00Z")
        self.t.advance(sid, "received",
                       f"2026-10-{min(end_day, 21):02d}T{int(end_h):02d}:00:00Z")
        self.t.result(sid, "negative",
                      f"2026-10-{min(end_day, 21):02d}T{int(end_h):02d}:30:00Z")

    def test_full_lifecycle(self):
        self._complete("S-1", "NRL", 12)
        s = self.t.samples["S-1"]
        self.assertEqual(s["terminal"], "resulted")
        self.assertEqual(s["result"], "negative")

    def test_out_of_order_and_impossible_times_refused(self):
        self.t.register("S-2", "CASE-1", "NRL", "2026-10-20T08:00:00Z")
        with self.assertRaises(LabTrackError):
            self.t.advance("S-2", "resulted", "2026-10-20T09:00:00Z")
        self.t.advance("S-2", "in-transit", "2026-10-20T09:00:00Z")
        with self.assertRaises(LabTrackError):
            self.t.advance("S-2", "received", "2026-10-20T07:00:00Z")
        with self.assertRaises(LabTrackError):
            self.t.advance("S-2", "collected", "2026-10-20T10:00:00Z")

    def test_baseline_learned_only_after_three(self):
        self._complete("S-1", "NRL", 12)
        self._complete("S-2", "NRL", 14)
        self.assertEqual(self.t.baseline("NRL")["state"], "learning")
        self._complete("S-3", "NRL", 16)
        b = self.t.baseline("NRL")
        self.assertEqual(b["state"], "ready")
        self.assertEqual(b["median_hours"], 14.5)

    def test_overdue_whisper_uses_learned_baseline(self):
        for i, h in enumerate((12, 14, 16)):
            self._complete(f"S-{i}", "NRL", h)
        self.t.register("S-late", "CASE-9", "NRL", "2026-10-22T08:00:00Z")
        # baseline 14 + margin 6 = 20h; check at 30h
        out = self.t.overdue("2026-10-23T14:00:00Z")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["sample"], "S-late")
        self.assertEqual(out[0]["stage"], "collected")

    def test_learning_lab_uses_floor_not_fake_eta(self):
        self.t.register("S-x", "CASE-1", "NEWLAB", "2026-10-20T08:00:00Z")
        # 30h wait, no baseline: floor 48+6 -> not overdue yet
        self.assertEqual(self.t.overdue("2026-10-21T14:00:00Z"), [])
        out = self.t.overdue("2026-10-23T00:00:00Z")  # 64h > 54h floor
        self.assertEqual(len(out), 1)

    def test_lost_sample_requires_reason_and_closes(self):
        self.t.register("S-9", "CASE-2", "NRL", "2026-10-20T08:00:00Z")
        with self.assertRaises(LabTrackError):
            self.t.mark_lost("S-9", " ", "2026-10-20T10:00:00Z")
        self.t.mark_lost("S-9", "courier vehicle broke down, cold chain "
                               "compromised", "2026-10-20T18:00:00Z")
        with self.assertRaises(LabTrackError):
            self.t.advance("S-9", "in-transit", "2026-10-20T19:00:00Z")

    def test_closed_sample_never_overdue(self):
        self._complete("S-1", "NRL", 12)
        self.assertEqual(self.t.overdue("2026-10-30T00:00:00Z"), [])


if __name__ == "__main__":
    unittest.main()
