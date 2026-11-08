"""wardtwin.py — the digital-twin ward board.

Before a manager closes ward 4 for renovation, moves nurses to the
ED, or opens an escalation ward, they ask the twin: what happens? The
twin simulates acuity x staffing x bed turnover over a declared
horizon and answers with STATED assumptions and STATED confidence —
a simulation that hides its assumptions is a propaganda tool.

Doctrine:
- ASSUMPTIONS ARE DECLARED INPUTS: admission rate, acuity mix, nurse
  ratios, turnover minutes all arrive as named parameters; the report
  echoes them verbatim.
- THE TWIN NEVER PRESCRIBES: it states consequences (queue lengths,
  breaches, hours over ratio) — the decision belongs to a named human.
- CONFIDENCE IS COMPUTED FROM SPREAD: multiple seeded runs; the band
  between p10 and p90 outcomes is reported. One run is an anecdote.
- ABSENCE IS NOT A VALUE: a ward with no acuity data simulates
  nothing — it refuses, stated.
"""

import random
import statistics

WARDTWIN_VERSION = "wardtwin@1.0.0"


class Error(Exception):
    pass


# acuity levels and the nursing hours per patient-day each demands
ACUITY_LEVELS = ("low", "medium", "high", "critical")
NURSE_HOURS_PER_DAY = {"low": 2.0, "medium": 4.0, "high": 8.0,
                       "critical": 16.0}


class WardTwin:
    def __init__(self, *, name, beds, horizon_days=14):
        if beds < 1 or horizon_days < 1:
            raise Error("a twin declares positive beds and horizon")
        self.name = name
        self.beds = beds
        self.horizon = horizon_days

    def simulate(self, *, admissions_per_day, acuity_mix,
                 nurse_hours_available_per_day, mean_turnover_hours,
                 runs=50, seed=42):
        """Simulate the ward over the horizon. acuity_mix maps level
        -> share of admissions (must sum to 1.0). Returns percentiles
        across seeded runs with the assumptions echoed."""
        if admissions_per_day < 0:
            raise Error("admission rate is a stated non-negative")
        if not acuity_mix:
            raise Error("no acuity mix — the twin refuses to invent "
                        "patient sickness")
        unknown = set(acuity_mix) - set(ACUITY_LEVELS)
        if unknown:
            raise Error(f"unknown acuity levels {sorted(unknown)}; "
                        f"levels are {ACUITY_LEVELS}")
        total = sum(acuity_mix.values())
        if abs(total - 1.0) > 0.001:
            raise Error(f"acuity mix sums to {total:.3f}, not 1.0 — "
                        "the twin does not normalise your assumptions "
                        "silently")
        if nurse_hours_available_per_day < 0 or \
                mean_turnover_hours < 0:
            raise Error("staffing and turnover assumptions are "
                        "non-negative, stated numbers")
        if runs < 3:
            raise Error("fewer than 3 runs is an anecdote, not a "
                        "simulation")

        assumptions = {"admissions_per_day": admissions_per_day,
                       "acuity_mix": dict(acuity_mix),
                       "nurse_hours_available_per_day":
                           nurse_hours_available_per_day,
                       "mean_turnover_hours": mean_turnover_hours,
                       "beds": self.beds,
                       "horizon_days": self.horizon,
                       "runs": runs, "seed": seed}

        peaks, breach_days, final_queues = [], [], []
        for run in range(runs):
            rng = random.Random(seed + run)
            occupied = 0
            queue = 0
            peak_queue = 0
            days_over_ratio = 0
            for _d in range(self.horizon):
                # discharges free beds after a turnover lag
                freed = sum(1 for _ in range(occupied)
                            if rng.random() < 1.0 / 3.0)  # ~3d stay
                occupied -= freed
                lagged = sum(1 for _ in range(freed) if rng.random()
                             < 24.0 / max(mean_turnover_hours, 0.5) / 24)
                occupied += freed - lagged   # lagged beds still cleaning
                # admissions arrive; those without a bed queue
                arrivals = rng.poisson(admissions_per_day) \
                    if hasattr(rng, "poisson") else \
                    _poisson(rng, admissions_per_day)
                for _ in range(arrivals):
                    if occupied < self.beds:
                        occupied += 1
                    else:
                        queue += 1
                # queued patients take beds as they free (next day)
                take = min(queue, max(0, self.beds - occupied))
                queue -= take
                occupied += take
                peak_queue = max(peak_queue, queue)
                # staffing adequacy against the census acuity
                demand = 0.0
                for level, share in acuity_mix.items():
                    demand += occupied * share * \
                        NURSE_HOURS_PER_DAY[level]
                if demand > nurse_hours_available_per_day:
                    days_over_ratio += 1
            peaks.append(peak_queue)
            breach_days.append(days_over_ratio)
            final_queues.append(queue)

        peaks.sort()
        breach_days.sort()

        def pct(series, p):
            return series[min(len(series) - 1,
                              int(len(series) * p))]

        spread = pct(peaks, 0.9) - pct(peaks, 0.1)
        confidence = ("narrow" if spread <= 2 else
                      "moderate" if spread <= 6 else "wide")
        return {"twin": self.name, "version": WARDTWIN_VERSION,
                "assumptions": assumptions,
                "peak_queue": {"p10": pct(peaks, 0.1),
                               "p50": pct(peaks, 0.5),
                               "p90": pct(peaks, 0.9)},
                "staffing_breach_days": {
                    "p50": pct(breach_days, 0.5),
                    "p90": pct(breach_days, 0.9)},
                "final_queue_p50": statistics.median(final_queues),
                "confidence": confidence,
                "confidence_reason":
                    f"p10-p90 spread on peak queue is {spread} beds "
                    f"across {runs} seeded runs — stated, not "
                    "smoothed",
                "verdict": "the twin states consequences; the "
                           "decision belongs to a named human"}


def _poisson(rng, lam):
    """Knuth's Poisson sampler — stdlib random has no poisson."""
    import math
    if lam <= 0:
        return 0
    big_l = math.exp(-lam)
    k, p = 0, 1.0
    while p > big_l:
        k += 1
        p *= rng.random()
    return k - 1


def what_if_close(ward_a, ward_b, *, closing_assumptions,
                  receiving_assumptions):
    """The manager's question: ward A closes, its admissions divert to
    ward B. Runs both twins; the deltas are stated side by side."""
    base = ward_b.simulate(**receiving_assumptions)
    diverted = dict(receiving_assumptions)
    diverted["admissions_per_day"] = \
        receiving_assumptions["admissions_per_day"] + \
        closing_assumptions["admissions_per_day"]
    after = WardTwin(name=f"{ward_b.name}+diverted",
                     beds=ward_b.beds,
                     horizon_days=ward_b.horizon).simulate(**diverted)
    return {"scenario": f"close {ward_a.name}, divert to "
                        f"{ward_b.name}",
            "before": base, "after": after,
            "delta_peak_queue_p50":
                after["peak_queue"]["p50"] - base["peak_queue"]["p50"],
            "delta_breach_days_p50":
                after["staffing_breach_days"]["p50"]
                - base["staffing_breach_days"]["p50"]}
