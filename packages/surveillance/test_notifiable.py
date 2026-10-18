#!/usr/bin/env python3
"""Notifiable-disease detection tests: IDSR rules, confirmation,
dedupe, anonymized aggregation, k-anonymity suppression."""
import unittest
from datetime import datetime, timedelta

from notifiable import (K_ANONYMITY_FLOOR, NOTIFIABLE, NotifiableDetector,
                        aggregate)

T0 = datetime(2026, 4, 1, 9, 0, 0)


def condition(rid, code, patient="P1", facility="FAC-1"):
    return {"resourceType": "Condition", "id": rid,
            "code": {"coding": [{"code": code}]},
            "subject": {"reference": f"Patient/{patient}"},
            "serviceProvider": {"reference": f"Location/{facility}"}}


def observation(rid, code, patient="P1", facility="FAC-1"):
    return {"resourceType": "Observation", "id": rid,
            "code": {"coding": [{"code": code}]},
            "subject": {"reference": f"Patient/{patient}"},
            "status": "final",
            "serviceProvider": {"reference": f"Location/{facility}"}}


class TestDetection(unittest.TestCase):
    def test_condition_fires_immediate_alert(self):
        d = NotifiableDetector()
        a = d.inspect(condition("c1", "measles"), T0, area="KANO-NMC")
        self.assertIsNotNone(a)
        self.assertEqual(a.disease, "measles")
        self.assertEqual(a.category, "immediate")
        self.assertEqual(a.status, "suspected")
        ev = a.to_event()
        self.assertEqual(ev["type"], "surveillance_alert")
        self.assertEqual(ev["priority"], "critical")

    def test_lab_observation_confirms(self):
        d = NotifiableDetector()
        a = d.inspect(observation("o1", "cholera-rdt-positive", patient="P9"), T0)
        self.assertEqual(a.status, "confirmed")
        self.assertEqual(a.disease, "cholera")

    def test_icd_prefix_match(self):
        d = NotifiableDetector()
        a = d.inspect(condition("c2", "A96.2"), T0)
        self.assertEqual(a.disease, "lassa_fever")

    def test_dedupe_same_episode(self):
        d = NotifiableDetector()
        first = d.inspect(condition("c3", "measles", patient="P2"), T0)
        again = d.inspect(condition("c4", "measles", patient="P2"),
                          T0 + timedelta(days=3))   # referral re-entry
        later = d.inspect(condition("c5", "measles", patient="P2"),
                          T0 + timedelta(days=40))  # genuinely new episode
        self.assertIsNotNone(first)
        self.assertIsNone(again)
        self.assertIsNotNone(later)

    def test_non_notifiable_ignored(self):
        d = NotifiableDetector()
        self.assertIsNone(d.inspect(condition("c6", "J06.9"), T0))  # common cold
        self.assertIsNone(d.inspect({"resourceType": "Patient", "id": "p"}, T0))

    def test_weekly_category_routine_priority(self):
        d = NotifiableDetector()
        a = d.inspect(condition("c7", "tuberculosis"), T0)
        self.assertEqual(a.category, "weekly")
        self.assertEqual(a.to_event()["priority"], "routine")


class TestAggregation(unittest.TestCase):
    def test_k_anonymity_suppression(self):
        d = NotifiableDetector()
        alerts = []
        for i in range(K_ANONYMITY_FLOOR):
            alerts.append(d.inspect(
                condition(f"c{i}", "measles", patient=f"P{i}"), T0, area="A"))
        few = d.inspect(condition("cx", "cholera", patient="PZ"),
                        T0, area="B")
        rows = aggregate([a for a in alerts if a] + [few])
        meas = next(r for r in rows if r["disease"] == "measles")
        chol = next(r for r in rows if r["disease"] == "cholera")
        self.assertFalse(meas["suppressed"])
        self.assertEqual(meas["count"], K_ANONYMITY_FLOOR)
        self.assertTrue(chol["suppressed"])
        self.assertIsNone(chol["count"])

    def test_anonymized_strips_identity(self):
        d = NotifiableDetector()
        a = d.inspect(condition("c8", "measles", patient="P5"), T0, area="A")
        anon = a.anonymized()
        self.assertNotIn("P5", str(anon))
        self.assertNotIn("source_resource", anon)


if __name__ == "__main__":
    unittest.main()
