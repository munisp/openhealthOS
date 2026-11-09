
"""surgecast.py — census-driven surge forecaster.

A bed manager who learns about the surge when the corridor is full has
no options left. This module projects bed/oxygen/staff demand from
admission history: a trailing baseline, a stated growth rate, and an
honest confidence band that WIDENS with the horizon — a point estimate
14 days out would be a lie.

Doctrine:
- CONFIDENCE IS A BAND, NOT A NUMBER: every projection carries
  low/high; the band widens with horizon_days.
- INSUFFICIENT HISTORY IS STATED: fewer than MIN_HISTORY_DAYS of
  admissions returns {'status': 'insufficient-history'}, never a
  fabricated projection.
- BREACHES ARE NAMED: when projected demand crosses capacity, the
  report names the day and the resource, not a vague 'high risk'.
"""

VERSION = "surgecast@1.0.0"
MIN_HISTORY_DAYS = 7
BAND_WIDEN_PER_DAY = 0.06  # each horizon day widens the band by 6%


class SurgeError(Exception):
    pass


def _baseline(history):
    """Trailing mean daily admissions."""
    return sum(history) / len(history)


def project(history, *, capacity, horizon_days, growth=0.0,
            resource="beds"):
    """history: list of daily admission counts (oldest first).
    capacity: hard ceiling for the resource.
    growth: declared fractional day-over-day change (0 = flat).
    Returns per-day projections with widening bands; breach days named."""
    if len(history) < MIN_HISTORY_DAYS:
        return {"status": "insufficient-history",
                "have_days": len(history),
                "need_days": MIN_HISTORY_DAYS,
                "note": "no projection fabricated — collect the baseline first"}
    if capacity <= 0:
        raise SurgeError("capacity must be positive")
    if horizon_days <= 0:
        raise SurgeError("horizon must be positive")
    base = _baseline(history)
    days = []
    breach_day = None
    for h in range(1, horizon_days + 1):
        mid = base * ((1 + growth) ** h)
        widen = 1 + BAND_WIDEN_PER_DAY * h
        # band around mid: low shrinks, high grows, both honest
        spread = base * 0.15 * widen
        lo, hi = max(0.0, mid - spread), mid + spread
        breach = hi > capacity
        if breach and breach_day is None:
            breach_day = h
        days.append({"day": h, "mid": round(mid, 1),
                     "low": round(lo, 1), "high": round(hi, 1),
                     "breaches_capacity": breach})
    out = {"status": "ok", "resource": resource, "capacity": capacity,
           "baseline": round(base, 1), "growth": growth,
           "horizon_days": horizon_days, "days": days}
    if breach_day is not None:
        out["breach"] = {"day": breach_day, "resource": resource,
                         "note": f"{resource} projected to exceed capacity "
                                 f"{capacity} on day {breach_day}"}
    return out


def staffing_need(projected_admissions, *, ratio):
    """Declared patients-per-staff ratio → staff needed. The ratio is a
    policy decision, stated on the output."""
    if ratio <= 0:
        raise SurgeError("staffing ratio must be positive")
    return {"ratio": ratio,
            "staff_needed": -(-int(projected_admissions) // int(ratio) or 1)}
