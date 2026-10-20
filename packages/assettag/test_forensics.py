#!/usr/bin/env python3
import unittest

from forensics import analyze, impossible_travel, scan_bursts, stale_scans


def ev(asset="EQ-1", facility="F1", actor="n1", at="2026-10-20T09:00:00Z", stale=False):
    return {"at": at, "asset": asset, "facility": facility, "action": "locate",
            "actor": actor, "location": "w1", "stale_tag": stale}


class TestImpossibleTravel(unittest.TestCase):
    def test_teleporting_asset_flagged(self):
        events = [ev(facility="F1", at="2026-10-20T09:00:00Z"),
                  ev(facility="F2", at="2026-10-20T09:30:00Z")]
        f = impossible_travel(events, 60)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["type"], "impossible_travel")

    def test_same_facility_moves_fine(self):
        events = [ev(facility="F1", at="2026-10-20T09:00:00Z"),
                  ev(facility="F1", at="2026-10-20T09:05:00Z")]
        self.assertEqual(impossible_travel(events, 60), [])

    def test_slow_legal_transfer_fine(self):
        events = [ev(facility="F1", at="2026-10-20T09:00:00Z"),
                  ev(facility="F2", at="2026-10-20T15:00:00Z")]
        self.assertEqual(impossible_travel(events, 60), [])

    def test_bad_timestamps_skipped(self):
        events = [ev(at="not-a-time"), ev(facility="F2", at="2026-10-20T09:00:00Z")]
        self.assertEqual(impossible_travel(events, 60), [])


class TestBursts(unittest.TestCase):
    def test_burst_flagged(self):
        events = [ev(asset=f"EQ-{i}", at=f"2026-10-20T09:00:{i:02d}Z") for i in range(12)]
        f = scan_bursts(events, 10)
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0]["actor"], "n1")

    def test_human_rhythm_fine(self):
        events = [ev(asset=f"EQ-{i}", at=f"2026-10-20T09:{i:02d}:00Z") for i in range(12)]
        self.assertEqual(scan_bursts(events, 10), [])


class TestStale(unittest.TestCase):
    def test_stale_pattern(self):
        events = [ev(stale=True), ev(asset="EQ-2", stale=True), ev(asset="EQ-3")]
        f = stale_scans(events)
        self.assertEqual(len(f), 1)
        self.assertIn("2 scans", f[0]["detail"])

    def test_clean(self):
        self.assertEqual(stale_scans([ev()]), [])


class TestAnalyze(unittest.TestCase):
    def test_clean_spine(self):
        events = [ev(), ev(asset="EQ-2", at="2026-10-20T10:00:00Z")]
        r = analyze(events)
        self.assertTrue(r["clean"])
        self.assertEqual(r["events"], 2)

    def test_dirty_spine(self):
        events = [ev(facility="F1", at="2026-10-20T09:00:00Z"),
                  ev(facility="F9", at="2026-10-20T09:10:00Z")]
        r = analyze(events)
        self.assertFalse(r["clean"])


if __name__ == "__main__":
    unittest.main()
