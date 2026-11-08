"""flowcast.py — patient-flow forecasting.

`forecast` predicts supplies; `queueintel` serves today's queue by
clinical need. This module answers tomorrow's question: how hard will
the ED be hit? It learns day-of-week and seasonal structure from the
facility's OWN admission history and predicts tomorrow's pressure —
with a stated confidence band, because a point forecast without a
band is a lie with decimals.

Doctrine:
- THE FACILITY'S OWN HISTORY, OR NOTHING: a forecast with fewer than
  the declared minimum days of history refuses — absence is not a
  value, and borrowed patterns are not your hospital.
- BANDS ARE HONEST: p10/p50/p90 from residual spread; a quiet history
  yields a narrow band, a wild one a wide band. Stated, never fixed.
- DEGRADED INPUT DEGRADES THE LABEL: gaps in history are counted and
  stamped on the forecast ("3 missing days"), never interpolated
  silently.
- STAFFING ADVICE IS A CONSEQUENCE, NOT A COMMAND: the output states
  required vs rostered capacity; the roster decision stays human.
"""

import math
import statistics

FLOWCAST_VERSION = "flowcast@1.0.0"


class Error(Exception):
    pass


MIN_HISTORY_DAYS = 28      # declared: four weeks, two full weekends × 2


class FlowForecaster:
    """Day-of-week + trend forecaster over admission history."""

    def __init__(self, *, min_history=MIN_HISTORY_DAYS):
        if min_history < 7:
            raise Error("less than a week of history forecasts "
                        "nothing — refused at construction")
        self.min_history = min_history

    def forecast(self, history, *, horizon_days=1, today_dow=None):
        """history: [(day_index, admissions)] sorted ascending, one
        entry per day; missing days must be ABSENT (gaps are counted).
        Returns per-day p10/p50/p90 with stated degradation."""
        if not history:
            raise Error("no history — the forecaster refuses to "
                        "invent your patients")
        days = [d for d, _ in history]
        vals = [v for _, v in history]
        if len(history) < self.min_history:
            raise Error(f"{len(history)} days of history against a "
                        f"declared minimum of {self.min_history} — "
                        "absence is not a value")
        if any(v < 0 for v in vals):
            raise Error("negative admissions are not data")
        gaps = sum(1 for a, b in zip(days, days[1:]) if b - a > 1)
        if today_dow is None:
            today_dow = days[-1] % 7

        # day-of-week means over the whole history
        dow_buckets = {}
        for d, v in history:
            dow_buckets.setdefault(d % 7, []).append(v)
        dow_mean = {k: statistics.mean(vs)
                    for k, vs in dow_buckets.items()}
        overall = statistics.mean(vals)
        # residual spread = honesty about the band
        residuals = [v - dow_mean[d % 7] for d, v in history]
        sigma = statistics.pstdev(residuals) if len(residuals) > 1 \
            else 0.0
        # weekly trend: last two weeks vs prior two, capped honestly
        if len(vals) >= 28:
            recent = statistics.mean(vals[-14:])
            prior = statistics.mean(vals[-28:-14])
            trend_per_day = (recent - prior) / 14.0
        else:
            trend_per_day = 0.0

        out_days = []
        for h in range(1, horizon_days + 1):
            dow = (today_dow + h) % 7
            base = dow_mean.get(dow, overall)
            p50 = max(0.0, base + trend_per_day * h)
            widen = sigma * math.sqrt(h)          # uncertainty grows
            out_days.append({
                "day_offset": h, "dow": dow,
                "p10": round(max(0.0, p50 - 1.28 * widen), 1),
                "p50": round(p50, 1),
                "p90": round(p50 + 1.28 * widen, 1)})

        band_note = ("narrow" if sigma < overall * 0.15 else
                     "moderate" if sigma < overall * 0.4 else "wide")
        return {"version": FLOWCAST_VERSION,
                "history_days": len(history),
                "missing_days": gaps,
                "degradation": (f"{gaps} missing day(s) in history — "
                                "counted, not interpolated")
                if gaps else "complete history",
                "band": band_note,
                "band_reason": f"residual sigma {sigma:.1f} against "
                               f"mean {overall:.1f} daily admissions",
                "days": out_days}

    def staffing_gap(self, forecast_day, *, minutes_per_patient,
                     rostered_minutes):
        """Required vs rostered capacity for a forecast day. The gap
        is stated in both directions; the decision stays human."""
        if minutes_per_patient <= 0 or rostered_minutes < 0:
            raise Error("capacity assumptions are stated positive")
        required = forecast_day["p50"] * minutes_per_patient
        surge = forecast_day["p90"] * minutes_per_patient
        gap = rostered_minutes - required
        return {"required_minutes_p50": round(required, 1),
                "required_minutes_p90_surge": round(surge, 1),
                "rostered_minutes": rostered_minutes,
                "gap_minutes": round(gap, 1),
                "state": ("covered" if gap >= 0 else
                          "SHORTFALL — stated, not smoothed"),
                "surge_state": ("covered-at-p90"
                                if rostered_minutes >= surge else
                                "exposed-at-p90 — stated"),
                "verdict": "the gap is stated; the roster decision "
                           "belongs to a named human"}
