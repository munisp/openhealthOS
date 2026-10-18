#!/usr/bin/env python3
"""Fraud detection: tripwires pend, small samples abstain, never auto-deny."""
import unittest

from frauddetect import FraudDetector


def claim(cid, amount=1000, tariff="CONSULT", provider="FAC-1", patient="PT-1", day=10, hour=9):
    return {"claim_id": cid, "amount_ngn": amount, "tariff_code": tariff,
            "provider_id": provider, "patient_id": patient,
            "ts": f"2026-10-{day:02d}T{hour:02d}:00:00Z"}


def baseline_tariff(n=12, amount=1000, day=1):
    return [claim(f"H-{i}", amount=amount + (i % 3) * 10, day=day) for i in range(n)]


class TestDetectors(unittest.TestCase):
    def setUp(self):
        self.fd = FraudDetector()

    def test_clean_claim_clears(self):
        rec = self.fd.analyze(claim("C-1", 1010), baseline_tariff())
        self.assertEqual(rec.action, "CLEAR")
        self.assertEqual(rec.reasons, [])

    def test_upcoding_pends_with_z(self):
        hist = baseline_tariff(n=12, amount=1000)
        rec = self.fd.analyze(claim("C-2", amount=5000), hist)
        self.assertEqual(rec.action, "PEND")
        self.assertTrue(any(r.startswith("UPCODING_Z") for r in rec.reasons))

    def test_small_baseline_abstains(self):
        rec = self.fd.analyze(claim("C-3", amount=9000), baseline_tariff(n=3))
        self.assertFalse(any(r.startswith("UPCODING_Z") for r in rec.reasons))

    def test_duplicate_within_window(self):
        hist = [claim("C-4", amount=700, tariff="MALARIA-RDT", day=10, hour=8)]
        rec = self.fd.analyze(claim("C-5", amount=700, tariff="MALARIA-RDT", day=10, hour=9), hist)
        self.assertEqual(rec.action, "PEND")
        self.assertEqual(rec.detail.get("duplicate_of"), "C-4")

    def test_patient_velocity_across_facilities(self):
        hist = [claim(f"V-{i}", provider=f"FAC-{i}", day=9) for i in range(1, 4)]
        rec = self.fd.analyze(claim("V-9", provider="FAC-9", day=10), hist)
        self.assertTrue(any(r.startswith("PATIENT_VELOCITY") for r in rec.reasons))

    def test_facility_volume_spike(self):
        hist = []
        for d in range(1, 9):  # 8 calm days of 2 claims each
            hist += [claim(f"S-{d}-{i}", provider="FAC-7", patient=f"PT-{d}{i}", day=d)
                     for i in range(2)]
        for i in range(20):  # today's flood, minus the one under review
            hist.append(claim(f"T-{i}", provider="FAC-7", patient=f"PT-T{i}", day=10))
        rec = self.fd.analyze(claim("T-99", provider="FAC-7", patient="PT-T99", day=10), hist)
        self.assertTrue(any(r.startswith("FACILITY_SPIKE") for r in rec.reasons))

    def test_never_denies(self):
        rec = self.fd.analyze(claim("C-9", amount=99999), baseline_tariff())
        self.assertIn(rec.action, ("CLEAR", "PEND"))

    # ------------------------------------------------ ML advisory seam (ADR-0014)
    def test_ml_advisory_pends_high_score(self):
        fd = FraudDetector(ml_scorer=lambda c, h: {"score": 0.93,
                                                   "model_version": "v9"})
        rec = fd.analyze(claim("ML-1"), [])
        self.assertEqual(rec.action, "PEND")
        self.assertTrue(any(r.startswith("ML_FRAUD_SCORE") for r in rec.reasons))
        self.assertEqual(rec.detail["ml"]["model_version"], "v9")
        self.assertEqual(rec.score, 0.93)

    def test_ml_low_score_does_not_pend(self):
        fd = FraudDetector(ml_scorer=lambda c, h: {"score": 0.2,
                                                   "model_version": "v9"})
        rec = fd.analyze(claim("ML-2"), [])
        self.assertEqual(rec.action, "CLEAR")

    def test_ml_failure_never_breaks_rules(self):
        def boom(c, h):
            raise RuntimeError("torch exploded")
        fd = FraudDetector(ml_scorer=boom)
        rec = fd.analyze(claim("ML-3", amount=700, tariff="MALARIA-RDT", day=10, hour=9),
                         [claim("ML-4", amount=700, tariff="MALARIA-RDT", day=10, hour=8)])
        self.assertEqual(rec.action, "PEND")  # duplicate rule still fires
        self.assertNotIn("ml", rec.detail)

    def test_ml_none_scorer_is_silent(self):
        fd = FraudDetector(ml_scorer=lambda c, h: None)
        rec = fd.analyze(claim("ML-5"), [])
        self.assertEqual(rec.action, "CLEAR")
        self.assertNotIn("ml", rec.detail)


if __name__ == "__main__":
    unittest.main()
