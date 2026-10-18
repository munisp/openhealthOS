#!/usr/bin/env python3
"""Predictive cold-chain maintenance — fix the fridge before the vaccines
(ADR-0013, innovation).

Vaccine fridges die slowly: seal wear, compressor fatigue, thermostat drift
show up as a slow temperature climb days before stock is lost. This package
reads a temperature series (2–8 °C is the safe band) and opens BMET tickets
on two signals:

  * EXCURSION  — already out of band (P1: act now, assess stock potency)
  * DRIFT      — statistically trending toward a breach, with a predicted
                 crossing time (P2: schedule maintenance before loss)

Tickets are platform events; danger=True routes to the critical queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

SAFE_MIN, SAFE_MAX = 2.0, 8.0
MIN_READINGS = 4
PREDICT_HORIZON_H = 72.0   # only predict breaches within 3 days


@dataclass
class Reading:
    ts: str
    celsius: float


@dataclass
class Ticket:
    fridge_id: str
    priority: str              # P1 excursion | P2 drift
    reason: str
    current_celsius: float
    predicted_breach_ts: str = ""

    def to_event(self) -> dict:
        return {"type": "coldchain_ticket",
                "payload": {"fridge_id": self.fridge_id, "priority": self.priority,
                            "reason": self.reason, "celsius": self.current_celsius,
                            "predicted_breach_ts": self.predicted_breach_ts},
                "danger": self.priority == "P1"}


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)


def _slope_per_hour(readings: list[Reading]) -> float:
    """Least-squares slope, °C per hour."""
    t0 = _dt(readings[0].ts)
    xs = [(_dt(r.ts) - t0).total_seconds() / 3600 for r in readings]
    ys = [r.celsius for r in readings]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def analyze(fridge_id: str, readings: list[Reading]) -> Ticket | None:
    """One fridge, one decision. Abstains below MIN_READINGS."""
    if len(readings) < MIN_READINGS:
        return None
    rs = sorted(readings, key=lambda r: r.ts)
    last = rs[-1]

    # 1) excursion — out of band right now
    if last.celsius < SAFE_MIN or last.celsius > SAFE_MAX:
        return Ticket(fridge_id, "P1",
                      f"EXCURSION {last.celsius:.1f}°C outside {SAFE_MIN:.0f}–{SAFE_MAX:.0f}°C band",
                      last.celsius)

    # 2) drift — trending toward a breach inside the horizon
    slope = _slope_per_hour(rs)
    if slope > 0.02:  # warming: when does it cross SAFE_MAX?
        hours = (SAFE_MAX - last.celsius) / slope
        if 0 < hours <= PREDICT_HORIZON_H:
            breach = _dt(last.ts).timestamp() + hours * 3600
            return Ticket(fridge_id, "P2",
                          f"DRIFT +{slope:.2f}°C/h — breaches {SAFE_MAX:.0f}°C in ~{hours:.0f}h",
                          last.celsius,
                          datetime.fromtimestamp(breach).isoformat() + "Z")
    if slope < -0.02:  # over-cooling: freezing destroys vaccines too
        hours = (last.celsius - SAFE_MIN) / (-slope)
        if 0 < hours <= PREDICT_HORIZON_H:
            breach = _dt(last.ts).timestamp() + hours * 3600
            return Ticket(fridge_id, "P2",
                          f"DRIFT {slope:.2f}°C/h — breaches {SAFE_MIN:.0f}°C in ~{hours:.0f}h",
                          last.celsius,
                          datetime.fromtimestamp(breach).isoformat() + "Z")
    return None
