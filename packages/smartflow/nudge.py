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
