#!/usr/bin/env python3
"""Adaptive z-score burst detection (innovation 3)."""
import unittest
from datetime import datetime, timedelta

from surveillance import Surveillance


def events(syn, area, day, n):
    return [{"syndrome": syn, "area": area, "ts": day.isoformat()}] * n


class AdaptiveTest(unittest.TestCase):
    def test_burst_above_baseline_alerts(self):
        s = Surveillance()
        today = datetime(2026, 10, 20)
        # 8 quiet weeks: ~3 measles-like cases/week in W-01
        for w in range(1, 9):
            d = today - timedelta(days=7 * w + 1)
            s.ingest(events("measles_like_rash", "W-01", d, 3), d)
        # this week: 12 — z far above 3 on a near-zero-variance baseline
        alerts = s.evaluate_adaptive(today)
        # note: ingest() already evaluated static thresholds per day;
        # evaluate_adaptive is the pure burst layer.
        s.ingest(events("measles_like_rash", "W-01", today, 12), today)
        alerts = s.evaluate_adaptive(today)
        self.assertTrue(any(a.syndrome == "measles_like_rash" and a.area == "W-01"
                            and a.severity == "alert" and "adaptive" in a.detail
                            for a in alerts), alerts)

    def test_normal_week_stays_silent(self):
        s = Surveillance()
        today = datetime(2026, 10, 20)
        for w in range(1, 9):
            d = today - timedelta(days=7 * w + 1)
            s.ingest(events("acute_watery_diarrhoea", "W-02", d, 12), d)
        s.ingest(events("acute_watery_diarrhoea", "W-02", today, 12), today)
        alerts = s.evaluate_adaptive(today)
        self.assertFalse(any("adaptive" in a.detail for a in alerts), alerts)

    def test_thin_history_defers_to_static_thresholds(self):
        s = Surveillance()
        today = datetime(2026, 10, 20)
        # only 2 baseline weeks: adaptive must stay silent (not enough data)
        for w in range(1, 3):
            d = today - timedelta(days=7 * w + 1)
            s.ingest(events("jaundice", "W-03", d, 1), d)
        s.ingest(events("jaundice", "W-03", today, 6), today)
        alerts = s.evaluate_adaptive(today)
        self.assertEqual([a for a in alerts if "adaptive" in a.detail], [])


if __name__ == "__main__":
    unittest.main()
