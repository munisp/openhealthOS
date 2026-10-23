"""nudge.py — next-action prediction with local acceptance learning.

Most clicks are navigation, not decisions: the nurse who opens a patient
with a pending result is going to acknowledge it. This module learns
WHICH action a given context usually resolves to — from this facility's
own history, stored locally, using only aggregate counts (no patient
content, no identifiers, ever).

Honesty rules:
- Beta-prior shrinkage: 1 accepted out of 1 is NOT a 100% recommendation;
  confidence grows with evidence. New contexts get honest "learning".
- Suggestions are advisory and ranked; the user always acts. A nudge
  never executes.
- A nudge that has been wrong more often than right is withdrawn, not
  quietly kept.

Context keys are coarse and structural by design: e.g.
("result_pending", "ward:general") — never ("patient", "PAT-123").
"""

# prior: act as if we already saw PRIOR_ACCEPT accepts and
# PRIOR_TOTAL-PRIOR_ACCEPT rejects — a weakly-optimistic prior that
# evaporates quickly under real evidence
PRIOR_ACCEPT, PRIOR_TOTAL = 1.0, 4.0
MIN_OBSERVATIONS = 5      # below this, the ranker says "learning", not a rate
WITHDRAW_BELOW = 0.3      # learned acceptance under 30% => stop suggesting


class NudgeRanker:
    def __init__(self):
        # (context, action) -> [accepts, shown]
        self._counts = {}
        self.decision_log = []  # exploration decisions w/ propensities

    def record(self, context, action, accepted):
        """One observation: the nudge was shown; did the user take it?"""
        c = self._counts.setdefault((context, action), [0, 0])
        c[1] += 1
        if accepted:
            c[0] += 1

    def score(self, context, action):
        accepts, shown = self._counts.get((context, action), [0, 0])
        if shown < MIN_OBSERVATIONS:
            return {"action": action, "state": "learning", "shown": shown}
        rate = (accepts + PRIOR_ACCEPT) / (shown + PRIOR_TOTAL)
        if rate < WITHDRAW_BELOW:
            return {"action": action, "state": "withdrawn",
                    "acceptance": round(rate, 3), "shown": shown}
        return {"action": action, "state": "ready",
                "acceptance": round(rate, 3), "shown": shown}

    def suggest(self, context, candidates):
        """Rank candidate actions for a context. Returns ready nudges
        first (best acceptance), then learning ones; withdrawn nudges are
        dropped entirely — a nudge the users keep refusing is noise."""
        scored = [self.score(context, a) for a in candidates]
        ready = sorted((s for s in scored if s["state"] == "ready"),
                       key=lambda s: -s["acceptance"])
        learning = [s for s in scored if s["state"] == "learning"]
        return {"context": context, "nudges": ready + learning,
                "withdrawn": [s["action"] for s in scored
                              if s["state"] == "withdrawn"]}

    def suggest_explore(self, context, candidates, epsilon=0.1, rng=None):
        """Epsilon-greedy suggestion WITH propensity logging — the
        exploration regime that makes offline policy evaluation
        (policyeval) possible.

        With probability epsilon, explores uniformly over non-withdrawn
        candidates (propensity = epsilon/len(eligible)); otherwise takes
        the argmax (propensity = 1 - epsilon [+ epsilon/len if the argmax
        could also be reached by exploration — it can]). Every suggestion
        appends a decision record to self.decision_log in exactly the
        shape from_nudge_log consumes: {context, action, propensity}.
        The reward (accepted/refused) is attached by record_outcome().

        A platform that never explores cannot prove a better policy
        exists; a platform that explores without logging propensities
        cannot prove it honestly. This does both halves."""
        import random as _random
        r = rng or _random
        scored = [self.score(context, a) for a in candidates]
        eligible = [s for s in scored if s["state"] != "withdrawn"]
        if not eligible:
            return {"context": context, "nudge": None,
                    "reason": "all candidates withdrawn"}
        ready = sorted((s for s in eligible if s["state"] == "ready"),
                       key=lambda s: -s["acceptance"])
        best = ready[0]["action"] if ready else eligible[0]["action"]
        if r.random() < epsilon:
            pick = r.choice(eligible)["action"]
            regime = "explore"
        else:
            pick, regime = best, "exploit"
        # honest propensity: P(pick) = (1-eps)*1[pick==best] + eps/len
        prop = epsilon / len(eligible)
        if pick == best:
            prop += (1 - epsilon)
        rec = {"context": context, "action": pick,
               "propensity": round(prop, 6), "regime": regime,
               "accepted": None}
        self.decision_log.append(rec)
        return {"context": context, "nudge": pick, "regime": regime,
                "propensity": rec["propensity"],
                "log_index": len(self.decision_log) - 1}

    def record_outcome(self, log_index, accepted):
        """Attach the reward to a logged suggestion. Refuses to attach
        twice — a reward that can be rewritten is not a reward."""
        rec = self.decision_log[log_index]
        if rec["accepted"] is not None:
            raise ValueError("outcome already recorded — reward is final")
        rec["accepted"] = bool(accepted)
        self.record(rec["context"], rec["action"], accepted)

    def decision_events(self):
        """Completed decision log entries in policyeval's shape."""
        return [{"context": r["context"], "action": r["action"],
                 "propensity": r["propensity"], "reward": 1.0 if r["accepted"] else 0.0}
                for r in self.decision_log if r["accepted"] is not None]
