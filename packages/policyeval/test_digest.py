#!/usr/bin/env python3
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from digest import render_digest  # noqa: E402
from policyeval import (EvalError, PromotionLog, evaluate,  # noqa: E402
                        from_batch_log)
from test_policyeval import synth_log, ALWAYS_B, ALWAYS_A  # noqa: E402


class TestBatchAdapter(unittest.TestCase):
    def test_deterministic_rule_log_refused_for_ope(self):
        with self.assertRaises(EvalError) as ctx:
            from_batch_log([{"batch_kind": "claims",
                             "suggested": "suggested-approve",
                             "confirmed": True, "propensity": 1.0}])
        self.assertIn("cannot evaluate alternatives", str(ctx.exception))

    def test_missing_propensity_refused(self):
        with self.assertRaises(EvalError):
            from_batch_log([{"batch_kind": "claims",
                             "suggested": "x", "confirmed": True}])

    def test_stochastic_rule_log_maps(self):
        out = from_batch_log([
            {"batch_kind": "claims", "suggested": "a",
             "confirmed": True, "propensity": 0.5},
            {"batch_kind": "claims", "suggested": "b",
             "confirmed": False, "propensity": 0.5}])
        self.assertEqual([e["reward"] for e in out], [1.0, 0.0])
        self.assertEqual(out[0]["context"], "claims")


class TestDigest(unittest.TestCase):
    def _registry_with_pending(self):
        log = synth_log(2000)
        res = evaluate(log, ALWAYS_B, seed=1, incumbent=0.4)
        reg = PromotionLog()
        entry = reg.submit("always-b-nudge", res, author="builder")
        return reg, entry, res

    def test_digest_renders_table_and_pending(self):
        reg, entry, res = self._registry_with_pending()
        held = evaluate(synth_log(2000), ALWAYS_A, seed=1, incumbent=0.6)
        md = render_digest("2026-10",
                           [{"name": "always-b-nudge", "result": res},
                            {"name": "always-a", "result": held}],
                           reg)
        self.assertIn("| always-b-nudge |", md)
        self.assertIn("| always-a |", md)
        self.assertIn(entry["fingerprint"], md)
        self.assertIn("1 ready for sign-off, 1 held", md)
        self.assertIn("promotion_log.approve(", md)

    def test_empty_pending_says_none(self):
        md = render_digest("2026-10", [], PromotionLog())
        self.assertIn("None awaiting signature.", md)

    def test_fingerprint_mismatch_warning_present(self):
        md = render_digest("2026-10", [], PromotionLog())
        self.assertIn("do not sign", md)


if __name__ == "__main__":
    unittest.main()
