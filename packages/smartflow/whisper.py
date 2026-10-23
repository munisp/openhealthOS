"""whisper.py — pre-submission anomaly check against facility history.

The productivity version of fraud detection: instead of denials after
submission, quiet pre-checks while the form is being filled. Learns the
facility's OWN baselines (per tariff code: median + MAD of historical
amounts) and whispers when a draft deviates — "this differs from your
usual" — catching errors before they become rejections.

Doctrine:
- Whisper, never block: severity is advisory; the user may proceed.
  Blocking is the adjudicator's job, with a human in the loop.
- Robust statistics only: median + MAD, because one outlier claim must
  not poison the baseline (fail-closed against its own training data).
- No baseline, no opinion: below the evidence floor the module is
  silent — a whisper that cries wolf on thin data trains users to
  ignore whispers.
"""

MIN_BASELINE = 5
WHISPER_SIGMA = 4.0  # robust z beyond which we whisper


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2


class Whisperer:
    def __init__(self):
        self._history = {}  # code -> [amounts]

    def observe(self, code, amount):
        """Learn from CONFIRMED (accepted/adjudicated) history only."""
        if amount <= 0:
            return  # nonsense never enters the baseline
        self._history.setdefault(code, []).append(amount)

    def check(self, code, amount):
        """Advisory verdict on a draft value."""
        hist = self._history.get(code, [])
        if len(hist) < MIN_BASELINE:
            return {"state": "no-baseline", "observations": len(hist)}
        med = _median(hist)
        mad = _median([abs(x - med) for x in hist]) or 1e-9
        z = 0.6745 * abs(amount - med) / mad  # robust z-score
        if z <= WHISPER_SIGMA:
            return {"state": "within-norm", "usual": med}
        direction = "above" if amount > med else "below"
        return {"state": "whisper",
                "message": f"this is unusual for your facility — "
                           f"{direction} the usual {med:g} for {code}",
                "usual": med, "robust_z": round(z, 2),
                "severity": "advisory"}  # never a block
