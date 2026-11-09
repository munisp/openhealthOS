
"""coldwitness.py — vaccine cold-chain excursion adjudicator.

A fridge logger produces a temperature series; someone must decide
whether the vaccines inside are still usable. That decision today is a
guess, a phone call, or quiet disposal of good stock. This module makes
it a VERDICT: per-antigen declared stability budgets (cumulative
degree-hours above threshold), applied deterministically, with the
verdict and the math on the record.

Doctrine:
- STABILITY BUDGETS ARE DATA: each antigen's threshold and cumulative
  excursion budget live in a ratified registry — never hardcoded per
  batch, never 'the pharmacist remembers'.
- THE VERDICT IS ARITHMETIC: degree-hours are computed from the
  logger series; usable / quarantine / discard follows from the
  declared bands, not from vibes.
- GAPS IN THE LOG ARE EXCURSIONS OF UNKNOWN SIZE: a logger gap is
  stated and treated as worst-case within its window — an unmonitored
  fridge is not a cold fridge.
"""

VERSION = "coldwitness@1.0.0"

VERDICTS = ("usable", "quarantine", "discard")
# default WHO-style stability table; deployments ratify their own
DEFAULT_STABILITY = {
    "bcg":        {"threshold_c": 8.0, "budget_degree_hours": 24.0},
    "measles":    {"threshold_c": 8.0, "budget_degree_hours": 12.0},
    "opv":        {"threshold_c": 8.0, "budget_degree_hours": 48.0},
    "hpv":        {"threshold_c": 8.0, "budget_degree_hours": 72.0},
    "covid-mrna": {"threshold_c": -15.0, "budget_degree_hours": 0.0},
}
QUARANTINE_FRACTION = 0.5  # >50% of budget consumed → quarantine


class ColdError(Exception):
    pass


class StabilityRegistry:
    def __init__(self, table=None):
        self._table = dict(table or DEFAULT_STABILITY)

    def ratify(self, antigen, *, threshold_c, budget_degree_hours, by):
        if budget_degree_hours < 0:
            raise ColdError("budget cannot be negative")
        self._table[antigen] = {"threshold_c": threshold_c,
                                "budget_degree_hours": budget_degree_hours,
                                "ratified_by": by}

    def spec(self, antigen):
        s = self._table.get(antigen)
        if s is None:
            raise ColdError(
                f"no stability budget for '{antigen}' — an unrated "
                f"antigen is quarantined, never guessed")
        return s


def degree_hours(readings, *, threshold_c, gap_worst_case_c=25.0):
    """readings: [(hour_offset, temp_c)] sorted; a gap > 1h between
    readings contributes the worst-case temperature for the gap."""
    if len(readings) < 2:
        raise ColdError("need at least two readings to integrate")
    dh = 0.0
    gaps = []
    for (h0, t0), (h1, t1) in zip(readings, readings[1:]):
        dt = h1 - h0
        if dt <= 0:
            raise ColdError("readings must be time-ordered")
        if dt > 1.0:
            gaps.append({"from_h": h0, "to_h": h1})
            dh += max(0.0, gap_worst_case_c - threshold_c) * dt
            continue
        excess = max(0.0, (t0 + t1) / 2 - threshold_c)
        dh += excess * dt
    return dh, gaps


def adjudicate(antigen, readings, *, registry=None, facility=""):
    reg = registry or StabilityRegistry()
    spec = reg.spec(antigen)
    # Measured excursions decide on their own; gap windows are separated
    # so worst-case gap arithmetic can HOLD stock (quarantine) but never
    # DESTROY it — discard requires the thermometer to have seen it.
    if len(readings) < 2:
        raise ColdError("need at least two readings to integrate")
    measured_dh, gap_dh, gaps = 0.0, 0.0, []
    for (h0, t0), (h1, t1) in zip(readings, readings[1:]):
        dt = h1 - h0
        if dt <= 0:
            raise ColdError("readings must be time-ordered")
        if dt > 1.0:
            gaps.append({"from_h": h0, "to_h": h1})
            gap_dh += max(0.0, 25.0 - spec["threshold_c"]) * dt
        else:
            measured_dh += max(0.0, (t0 + t1) / 2
                               - spec["threshold_c"]) * dt
    dh = measured_dh + gap_dh
    budget = spec["budget_degree_hours"]
    if measured_dh > budget:
        verdict = "discard"           # measured, not assumed
    elif gaps or dh > budget * QUARANTINE_FRACTION:
        verdict = "quarantine"        # unknown truth holds stock
    else:
        verdict = "usable"
    return {"antigen": antigen, "facility": facility,
            "degree_hours": round(dh, 2), "budget": budget,
            "budget_consumed": round(dh / budget, 3) if budget else 1.0,
            "logger_gaps": gaps, "verdict": verdict,
            "note": "verdict is arithmetic on the logger series; "
                    "gaps are treated as worst-case and always hold stock"}
