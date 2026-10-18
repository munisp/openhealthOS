#!/usr/bin/env python3
"""Surveillance tests: thresholds, k-anonymity, dedupe, DP noise bounds."""
import unittest
from datetime import datetime

from surveillance import (K_ANONYMITY_FLOOR, Surveillance,
                          syndromes_from_triage)


def ev(syn, area, day):
    return {"syndrome": syn, "area": area, "ts": f"2026-10-{day:02d}T09:00:00Z"}


class TestThresholds(unittest.TestCase):
    def test_below_threshold_silent(self):
        s = Surveillance()
        alerts = s.ingest([ev("measles_like_rash", "KAWO", 10),
                           ev("measles_like_rash", "KAWO", 11)],
                          datetime(2026, 10, 12))
        self.assertEqual(alerts, [])

    def test_threshold_crossing_alerts(self):
        s = Surveillance()
        events = [ev("measles_like_rash", "KAWO", d) for d in (8, 9, 10, 11, 12)]
        alerts = s.ingest(events, datetime(2026, 10, 12))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].syndrome, "measles_like_rash")
        self.assertEqual(alerts[0].area, "KAWO")

    def test_window_slides_off_old_events(self):
        s = Surveillance()
        events = [ev("measles_like_rash", "KAWO", 1) for _ in range(5)]  # too old by day 20
        alerts = s.ingest(events, datetime(2026, 10, 20))
        self.assertEqual(alerts, [])

    def test_one_alert_per_window(self):
        s = Surveillance()
        events = [ev("measles_like_rash", "KAWO", 10) for _ in range(6)]
        first = s.ingest(events, datetime(2026, 10, 12))
        second = s.evaluate(datetime(2026, 10, 12))  # same window
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])  # no alarm fatigue

    def test_alert_event_is_critical_priority(self):
        s = Surveillance()
        events = [ev("acute_watery_diarrhoea", "MAKERA", 10) for _ in range(22)]
        alerts = s.ingest(events, datetime(2026, 10, 12))
        big = [a for a in alerts if a.severity == "alert"]
        self.assertTrue(big)
        self.assertTrue(big[0].to_event()["danger"])  # rides the critical queue


class TestPrivacy(unittest.TestCase):
    def test_k_anonymity_floor_suppresses_exact_count(self):
        s = Surveillance()
        # threshold 3, but only 4 cases — below the k=5 floor
        events = [ev("measles_like_rash", "TINY-WARD", 10) for _ in range(4)]
        alerts = s.ingest(events, datetime(2026, 10, 12))
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].severity, "watch")
        self.assertEqual(alerts[0].count, K_ANONYMITY_FLOOR)  # never the true small count

    def test_dp_noise_stays_bounded(self):
        s = Surveillance(dp_epsilon=1.0)
        events = [ev("acute_respiratory_infection", "KAWO", 10) for _ in range(40)]
        alerts = s.ingest(events, datetime(2026, 10, 12))
        self.assertTrue(alerts)
        # Laplace(1/eps) — a 40-count cell must stay in a sane band
        self.assertTrue(20 <= alerts[0].count <= 60)


class TestTriageMapping(unittest.TestCase):
    def test_danger_flags_map_to_syndromes(self):
        self.assertEqual(syndromes_from_triage(["rash_with_fever", "fast_breathing"]),
                         ["measles_like_rash", "acute_respiratory_infection"])
        self.assertEqual(syndromes_from_triage(["unknown_flag"]), [])


if __name__ == "__main__":
    unittest.main()
