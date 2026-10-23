#!/usr/bin/env python3
"""The loop closed end-to-end: explore with logged propensities ->
evaluate a candidate policy offline -> govern the promotion."""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "policyeval"))
from nudge import NudgeRanker  # noqa: E402
from policyeval import evaluate  # noqa: E402


def drive(ranker, n, epsilon, seed, true_rates):
    """Simulate a ward: ranker suggests (epsilon-greedy), users accept at
    the action's true rate."""
    rng = random.Random(seed)
    for _ in range(n):
        s = ranker.suggest_explore("dispense",
                                   ["print-label", "check-interactions",
                                    "restock-flag"],
                                   epsilon=epsilon, rng=rng)
        if s["nudge"] is None:
            continue
        accepted = rng.random() < true_rates[s["nudge"]]
        ranker.record_outcome(s["log_index"], accepted)


class TestExploreLoop(unittest.TestCase):
    def test_propensities_logged_honestly(self):
        r = NudgeRanker()
        rng = random.Random(1)
        s = r.suggest_explore("dispense", ["a", "b"], epsilon=0.2, rng=rng)
        # exploit arm: 0.8 + 0.1; explore-only arm: 0.1
        self.assertIn(s["propensity"], (0.9, 0.1))
        self.assertEqual(len(r.decision_log), 1)

    def test_outcome_final(self):
        r = NudgeRanker()
        s = r.suggest_explore("c", ["a", "b"], rng=random.Random(2))
        r.record_outcome(s["log_index"], True)
        with self.assertRaises(ValueError):
            r.record_outcome(s["log_index"], False)

    def test_all_withdrawn_is_honest_none(self):
        r = NudgeRanker()
        for _ in range(20):
            r.record("dispense", "annoy", False)  # drives to withdrawn
        out = r.suggest_explore("dispense", ["annoy"], rng=random.Random(3))
        self.assertIsNone(out["nudge"])
        self.assertIn("withdrawn", out["reason"])

    def test_ope_recovers_better_policy_from_exploration_log(self):
        """The scientific claim of the loop: a ranker exploring at eps=0.2
        produces a log on which policyeval can prove the good nudge beats
        the bad one — conservative CI, not vibes."""
        r = NudgeRanker()
        drive(r, 4000, epsilon=0.2, seed=11,
              true_rates={"print-label": 0.75, "check-interactions": 0.55,
                          "restock-flag": 0.25})
        events = r.decision_events()
        self.assertGreater(len(events), 3000)
        good = evaluate(events, lambda ctx: "print-label",
                        seed=5, incumbent=0.5)
        bad = evaluate(events, lambda ctx: "restock-flag",
                       seed=5, incumbent=0.5)
        self.assertEqual(good["verdict"]["verdict"], "promote")
        self.assertEqual(bad["verdict"]["verdict"], "hold")
        # estimate is in the right neighbourhood (exploration-heavy log
        # understates a pure-exploit policy — SNIPS corrects)
        self.assertAlmostEqual(good["estimate_snips"], 0.75, delta=0.1)

    def test_pure_exploit_log_cannot_evaluate(self):
        """Doctrine pinned: without exploration, the log contains only the
        argmax arm and evaluating any other policy refuses — the system
        cannot lie about policies it never tried."""
        r = NudgeRanker()
        drive(r, 300, epsilon=0.0, seed=13,
              true_rates={"print-label": 0.7, "check-interactions": 0.5,
                          "restock-flag": 0.3})
        events = r.decision_events()
        taken = {e["action"] for e in events}
        other = [a for a in ("print-label", "check-interactions",
                             "restock-flag") if a not in taken]
        if other:  # exploit collapsed to one arm
            from policyeval import EvalError
            with self.assertRaises(EvalError):
                evaluate(events, lambda ctx: other[0])


if __name__ == "__main__":
    unittest.main()
