"""Golden-workflow tests for smartflow: next-action learning + batch review."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nudge import NudgeRanker, MIN_OBSERVATIONS  # noqa: E402
from batch import BatchReview  # noqa: E402


class TestNudgeRanker(unittest.TestCase):
    def test_learning_below_min_observations(self):
        r = NudgeRanker()
        for _ in range(MIN_OBSERVATIONS - 1):
            r.record("result_pending", "result.ack", True)
        s = r.suggest("result_pending", ["result.ack"])
        self.assertEqual(s["nudges"][0]["state"], "learning")
        self.assertNotIn("acceptance", s["nudges"][0])  # no fake precision

    def test_learned_ranking_prefers_accepted_action(self):
        r = NudgeRanker()
        for _ in range(20):
            r.record("result_pending", "result.ack", True)
            r.record("result_pending", "open.chart", False)
        s = r.suggest("result_pending", ["result.ack", "open.chart"])
        self.assertEqual(s["nudges"][0]["action"], "result.ack")
        self.assertEqual(s["nudges"][0]["state"], "ready")
        self.assertGreater(s["nudges"][0]["acceptance"], 0.8)
        self.assertIn("open.chart", s["withdrawn"])  # refused too often

    def test_shrinkage_keeps_small_samples_humble(self):
        r = NudgeRanker()
        for _ in range(MIN_OBSERVATIONS):
            r.record("ctx", "a", True)
        s = r.score("ctx", "a")
        # 5/5 observed, but prior shrinkage keeps it below 1.0
        self.assertLess(s["acceptance"], 1.0)
        self.assertGreater(s["acceptance"], 0.6)

    def test_contexts_are_independent(self):
        r = NudgeRanker()
        for _ in range(10):
            r.record("lab_pending", "result.ack", True)
        s = r.suggest("appointment_due", ["appointment.confirm"])
        self.assertEqual(s["nudges"][0]["state"], "learning")


class TestBatchReview(unittest.TestCase):
    def _rows(self):
        rows = [{"id": f"r{i}", "drug": "artemether", "qty": 100, "exp_ok": True}
                for i in range(38)]
        rows += [{"id": "bad1", "drug": "artemether", "qty": -5, "exp_ok": True},
                 {"id": "bad2", "drug": "", "qty": 10, "exp_ok": False}]
        return rows

    def _batch(self):
        checks = {"qty-positive": lambda r: r["qty"] > 0,
                  "drug-named": lambda r: bool(r["drug"]),
                  "expiry-ok": lambda r: r["exp_ok"]}
        return BatchReview("stocktake", checks).load(self._rows())

    def test_pile_becomes_one_decision_plus_exceptions(self):
        b = self._batch()
        v = b.review()
        self.assertEqual(v["total"], 40)
        self.assertEqual(v["suggested_approve"]["count"], 38)
        self.assertEqual(len(v["exceptions"]), 2)
        # exceptions name their failed checks — no mystery rows
        self.assertIn("qty-positive", v["exceptions"][0]["failed"])

    def test_confirm_records_accountability(self):
        b = self._batch()
        r = b.confirm("pharmacist-amina", rule_version=1, now=1234)
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["approved"]), 38)
        self.assertEqual(sorted(r["exceptions_left"]), ["bad1", "bad2"])
        self.assertEqual(b.confirmed["reviewer"], "pharmacist-amina")

    def test_fail_closed_stale_rules_and_double_confirm(self):
        b = self._batch()
        self.assertEqual(b.confirm("x", rule_version=99)["reason"],
                         "stale-rules-recheck-required")
        b.confirm("x", rule_version=1)
        self.assertEqual(b.confirm("y", rule_version=1)["reason"],
                         "already-confirmed")

    def test_no_row_is_ever_auto_approved(self):
        b = self._batch()
        self.assertIsNone(b.confirmed)  # nothing approved before confirm()


if __name__ == "__main__":
    unittest.main()
