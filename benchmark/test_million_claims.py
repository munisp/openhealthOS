"""Tests for the Million-Claim Challenge harness."""

import json
import unittest

from million_claims import check_invariants, generate_claims, run


class TestGeneration(unittest.TestCase):
    def test_deterministic_same_seed(self):
        a = generate_claims(500, seed=7)
        b = generate_claims(500, seed=7)
        self.assertEqual(a, b)

    def test_different_seed_differs(self):
        a = generate_claims(500, seed=7)
        b = generate_claims(500, seed=8)
        self.assertNotEqual(a, b)

    def test_mix_produces_all_kinds(self):
        kinds = {c["_kind"] for c in generate_claims(5000, seed=42)}
        self.assertEqual(kinds, {"clean", "frequency_breach", "duplicate",
                                 "price_drift", "benefit_violation", "inactive_member"})

    def test_clean_is_the_majority(self):
        claims = generate_claims(5000, seed=42)
        clean = sum(1 for c in claims if c["_kind"] == "clean")
        self.assertGreater(clean / len(claims), 0.7)


class TestInvariants(unittest.TestCase):
    def test_clean_approval_passes(self):
        problems = check_invariants({
            "decision": "APPROVE", "reasons": ["OK"],
            "allowed_ngn": 1000, "copay_ngn": 100, "payable_ngn": 900,
        })
        self.assertEqual(problems, [])

    def test_silent_verdict_flagged(self):
        problems = check_invariants({"decision": "DENY", "reasons": []})
        self.assertTrue(any("silent" in p for p in problems))

    def test_money_inconsistency_flagged(self):
        problems = check_invariants({
            "decision": "APPROVE", "reasons": ["OK"],
            "allowed_ngn": 1000, "copay_ngn": 100, "payable_ngn": 800,
        })
        self.assertTrue(any("money" in p for p in problems))


class TestRun(unittest.TestCase):
    def test_small_run_zero_violations(self):
        report = run(generate_claims(2000, seed=42))
        self.assertEqual(report["claims"], 2000)
        self.assertEqual(report["invariant_violation_count"], 0)
        total = sum(report["decision_distribution"].values())
        self.assertEqual(total, 2000)
        self.assertGreater(report["throughput_claims_per_sec"], 0)
        # All four latency percentiles present and ordered.
        lat = report["latency_ms"]
        self.assertLessEqual(lat["p50"], lat["p95"])
        self.assertLessEqual(lat["p95"], lat["p99"])

    def test_report_is_json_serializable(self):
        json.dumps(run(generate_claims(200, seed=1)))

    def test_decision_mix_reflects_workload(self):
        report = run(generate_claims(10000, seed=42))
        dist = report["decision_distribution"]
        # Clean workload dominates → approvals dominate.
        self.assertGreater(dist["APPROVE"], dist["PEND"])
        self.assertGreater(dist["APPROVE"], dist["DENY"])
        # Adversarial cases actually fire — the mix is not decorative.
        self.assertGreater(dist["PEND"], 0)
        self.assertGreater(dist["DENY"], 0)


if __name__ == "__main__":
    unittest.main()
