"""defaults.py — form pre-fill that learns, facility-local.

80% of forms repeat the last entry for the same context. This learns
per-(context, field) value frequencies from CONFIRMED submissions (user
corrections are the training signal) and pre-fills the dominant value.

Privacy doctrine: values learned are STRUCTURED fields only (ward,
dosage unit, tariff code). Free-text fields are never learned — that is
what the free-text gate is for, and this module refuses them.

Honesty rules:
- A default shows its evidence ("used 14 of last 20") — the user sees
  WHY the form is pre-filled, not just that it is.
- Below the evidence floor there is no default, not a guess.
- Pre-filled is never pre-submitted: confirm() is the only way a
  default becomes a record, and corrections teach immediately.
- A default that keeps getting corrected loses dominance and steps down.
"""

MIN_EVIDENCE = 3         # observations before a default may appear
DOMINANCE = 0.6          # value must hold 60% of the window to pre-fill
WINDOW = 20              # rolling evidence window per (context, field)
STRUCTURED_ONLY = True   # free text is never learnable here


class DefaultsLearner:
    def __init__(self, free_text_fields=frozenset()):
        self._obs = {}     # (context, field) -> [values...] rolling
        self.free_text_fields = set(free_text_fields)

    def _key(self, context, field):
        if field in self.free_text_fields:
            raise ValueError(f"free-text field is not learnable: {field}")
        return (context, field)

    def observe(self, context, field, value):
        """Record the CONFIRMED value (what the user actually submitted)."""
        k = self._key(context, field)
        obs = self._obs.setdefault(k, [])
        obs.append(value)
        del obs[:-WINDOW]

    def default(self, context, field):
        """The learned default, with evidence — or an honest nothing."""
        obs = self._obs.get(self._key(context, field), [])
        if len(obs) < MIN_EVIDENCE:
            return {"state": "insufficient-evidence", "observations": len(obs)}
        from collections import Counter
        top, n = Counter(obs).most_common(1)[0]
        share = n / len(obs)
        if share < DOMINANCE:
            return {"state": "no-dominant-value",
                    "observations": len(obs),
                    "top_share": round(share, 3)}
        return {"state": "suggested", "value": top,
                "evidence": f"used {n} of last {len(obs)}",
                "share": round(share, 3)}

    def prefill(self, context, fields):
        """{field: suggestion} for a form — only fields with a real
        suggestion appear; the rest stay blank, honestly."""
        out = {}
        for f in fields:
            try:
                d = self.default(context, f)
            except ValueError:
                continue  # free text silently stays manual
            if d["state"] == "suggested":
                out[f] = d
        return out
