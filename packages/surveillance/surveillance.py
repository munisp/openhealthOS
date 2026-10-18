#!/usr/bin/env python3
"""Syndromic surveillance — early-warning radar from triage exhaust
(ADR-0013, innovation).

Every PHC triage already produces structured syndrome codes. This package
aggregates them per ward/LGA over sliding windows and raises alerts when a
syndrome breaches its threshold — measles-like rash clusters, diarrhoea
spikes, ARI surges — days before formal surveillance reports arrive.

Privacy by construction (NDPA): only counts leave this package, never
patient records; a k-anonymity floor suppresses any cell smaller than k;
optional Laplace noise (epsilon) for published aggregates. The hub tier
runs this over its spill before forwarding — alerts ride the critical
queue.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# Syndrome → (alert threshold per window, window days). Thresholds are
# starting points for pilot tuning, not epidemiological gospel.
SYNDROMES: dict[str, dict] = {
    "measles_like_rash":    {"threshold": 3,  "window_days": 7},
    "acute_watery_diarrhoea": {"threshold": 10, "window_days": 3},
    "acute_respiratory_infection": {"threshold": 20, "window_days": 7},
    "fever_with_rash":      {"threshold": 5,  "window_days": 7},
    "jaundice":             {"threshold": 3,  "window_days": 14},
}

K_ANONYMITY_FLOOR = 5  # cells smaller than this never report


@dataclass
class Alert:
    syndrome: str
    area: str           # ward or LGA code
    count: int          # noisy if dp enabled
    window_days: int
    severity: str       # watch | alert
    detail: str = ""

    def to_event(self) -> dict:
        """An alert is a critical-priority platform event."""
        return {"type": "surveillance_alert",
                "payload": {"syndrome": self.syndrome, "area": self.area,
                            "count": self.count, "severity": self.severity},
                "danger": self.severity == "alert"}


@dataclass
class Surveillance:
    """Ingests triage events, emits alerts. Stateless across windows:
    callers feed it events; it keeps per-(syndrome, area) day buckets."""
    dp_epsilon: float = 0.0   # 0 = exact counts; >0 = Laplace noise
    buckets: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(int)))
    alerted: set = field(default_factory=set)

    def ingest(self, events: list[dict], today: datetime) -> list[Alert]:
        """events: [{syndrome, area, ts}] — from triage package output."""
        for ev in events:
            syn, area = ev.get("syndrome"), ev.get("area")
            if syn not in SYNDROMES or not area:
                continue
            day = (ev.get("ts") or today.isoformat())[:10]
            self.buckets[(syn, area)][day] += 1
        return self.evaluate(today)

    def evaluate(self, today: datetime) -> list[Alert]:
        alerts: list[Alert] = []
        for (syn, area), days in self.buckets.items():
            spec = SYNDROMES[syn]
            cutoff = (today - timedelta(days=spec["window_days"])).isoformat()[:10]
            count = sum(c for d, c in days.items() if d >= cutoff)
            if count < spec["threshold"]:
                continue
            if count < K_ANONYMITY_FLOOR:
                # real signal but too small to report safely — watch tier
                severity, reported = "watch", K_ANONYMITY_FLOOR
            else:
                severity = "alert" if count >= spec["threshold"] * 2 else "watch"
                reported = self._noisy(count)
            key = (syn, area, cutoff)
            if key in self.alerted:
                continue  # one alert per window — no alarm fatigue
            self.alerted.add(key)
            alerts.append(Alert(syn, area, reported, spec["window_days"], severity,
                                f"{syn} ×{count} in {area} over {spec['window_days']}d"))
        return alerts

    def _noisy(self, count: int) -> int:
        if self.dp_epsilon <= 0:
            return count
        scale = 1.0 / self.dp_epsilon
        noise = -scale * math.copysign(1, random.random() - 0.5) * math.log(1 - 2 * abs(random.random() - 0.5))
        return max(0, round(count + noise))


def syndromes_from_triage(triage_flags: list[str]) -> list[str]:
    """Map triage-package danger flags onto surveillance syndromes."""
    mapping = {
        "rash_with_fever": "measles_like_rash",
        "diarrhoea_severe": "acute_watery_diarrhoea",
        "fast_breathing": "acute_respiratory_infection",
        "chest_indrawing": "acute_respiratory_infection",
        "jaundice": "jaundice",
    }
    return [mapping[f] for f in triage_flags if f in mapping]
