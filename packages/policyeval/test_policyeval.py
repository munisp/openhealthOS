#!/usr/bin/env python3
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from policyeval import (EvalError, PromotionLog, evaluate,  # noqa: E402
                        from_nudge_log, validate_log)


def synth_log(n, bad_frac=0.0, seed=7):
    """Logged behaviour: uniform random over {a,b} (propensity 0.5).
    True rewards: action 'b' is better (0.8 accept vs 0.4)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        action = rng.choice(["a", "b"])
        p_accept = 0.8 if action == "b" else 0.4
        if i < int(n * bad_frac):
            out.append({"context": f"c{i}", "action": action,
                        "propensity": 0.5, "reward": None})
        else:
            out.append({"context": f"c{i}", "action": action,
                        "propensity": 0.5,
                        "reward": 1.0 if rng.random() < p_accept else 0.0})
    return [e for e in out if e["reward"] is not None] if bad_frac == 0 else out


ALWAYS_B = lambda ctx: "b"  # noqa: E731
ALWAYS_A = lambda ctx: "a"  # noqa: E731


class TestValidate(unittest.TestCase):
    def test_missing_fields_and_bad_propensity(self):
        issues = validate_log([{"context": "c"}, {"context": "c",
                               "action": "a", "propensity": 0.0,
                               "reward": 1}])
        self.assertTrue(any("missing" in i for i in issues))
        self.assertTrue(any("outside" in i for i in issues))

    def test_clean_log_passes(self):
        self.assertEqual(validate_log(synth_log(20)), [])


class TestEvaluate(unittest.TestCase):
    def test_better_policy_estimates_higher(self):
        log = synth_log(2000)
        rb = evaluate(log, ALWAYS_B, seed=1)
        ra = evaluate(log, ALWAYS_A, seed=1)
        self.assertGreater(rb["estimate_ips"], ra["estimate_ips"])
        self.assertAlmostEqual(rb["estimate_ips"], 0.8, delta=0.08)
        self.assertAlmostEqual(ra["estimate_ips"], 0.4, delta=0.08)

    def test_promotion_verdict_conservative(self):
        log = synth_log(2000)
        rb = evaluate(log, ALWAYS_B, seed=1, incumbent=0.4)
        self.assertEqual(rb["verdict"]["verdict"], "promote")
        ra = evaluate(log, ALWAYS_A, seed=1, incumbent=0.6)
        self.assertEqual(ra["verdict"]["verdict"], "hold")

    def test_small_log_is_insufficient_evidence(self):
        out = evaluate(synth_log(10), ALWAYS_B, seed=1, incumbent=0.4)
        self.assertEqual(out["state"], "insufficient-evidence")
        self.assertEqual(out["verdict"]["verdict"], "hold")

    def test_refusal_budget_fails_closed(self):
        log = synth_log(100)
        # candidate picks 'c' — an action the log never took
        with self.assertRaises(EvalError) as ctx:
            evaluate(log, lambda ctx: "c")
        self.assertIn("refusing to estimate", str(ctx.exception))

    def test_zero_propensity_events_refused(self):
        log = [{"context": "c", "action": "b", "propensity": 0.0,
                "reward": 1.0} for _ in range(50)]
        with self.assertRaises(EvalError):
            evaluate(log, ALWAYS_B)

    def test_fingerprints_present(self):
        out = evaluate(synth_log(200), ALWAYS_B, seed=1)
        self.assertIn("log", out["fingerprints"])
        self.assertIn("policy_decisions", out["fingerprints"])


class TestNudgeAdapter(unittest.TestCase):
    def test_adapter_maps_rewards(self):
        recs = [{"context": "dispense", "action": "print-label",
                 "accepted": True, "propensity": 0.25},
                {"context": "dispense", "action": "print-label",
                 "accepted": False, "propensity": 0.25}]
        out = from_nudge_log(recs)
        self.assertEqual([e["reward"] for e in out], [1.0, 0.0])

    def test_unlogged_propensity_refused(self):
        with self.assertRaises(EvalError) as ctx:
            from_nudge_log([{"context": "c", "action": "a",
                             "accepted": True}])
        self.assertIn("fiction", str(ctx.exception))


class TestPromotionGovernance(unittest.TestCase):
    def _promotable(self):
        log = synth_log(2000)
        res = evaluate(log, ALWAYS_B, seed=1, incumbent=0.4)
        self.assertEqual(res["verdict"]["verdict"], "promote")
        return res

    def test_full_governance_lifecycle(self):
        reg = PromotionLog()
        entry = reg.submit("always-b-nudge", self._promotable(),
                           author="builder")
        live = reg.approve(entry["fingerprint"], approver="dr-okafor",
                           role="consultant",
                           note="clears incumbent with margin")
        self.assertEqual(live["state"], "live")

    def test_hold_verdict_cannot_enter_governance(self):
        reg = PromotionLog()
        res = evaluate(synth_log(2000), ALWAYS_A, seed=1, incumbent=0.6)
        with self.assertRaises(EvalError) as ctx:
            reg.submit("always-a", res, author="builder")
        self.assertIn("route around", str(ctx.exception))

    def test_author_cannot_self_approve(self):
        reg = PromotionLog()
        entry = reg.submit("x", self._promotable(), author="builder")
        with self.assertRaises(EvalError):
            reg.approve(entry["fingerprint"], approver="builder",
                        role="consultant", note="mine")

    def test_role_and_note_required(self):
        reg = PromotionLog()
        entry = reg.submit("x", self._promotable(), author="builder")
        with self.assertRaises(EvalError):
            reg.approve(entry["fingerprint"], approver="intern",
                        role="intern", note="ok")
        with self.assertRaises(EvalError):
            reg.approve(entry["fingerprint"], approver="dr-okafor",
                        role="consultant", note="  ")


if __name__ == "__main__":
    unittest.main()
