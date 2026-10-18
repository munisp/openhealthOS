#!/usr/bin/env python3
"""Claims fraud anomaly detection — signals into the PEND lane
(ADR-0013, innovation).

Statistical tripwires over claims history. Four detectors:
  * UPCODING_Z        — amount z-score vs that tariff's baseline
  * DUPLICATE_CLAIM   — same patient/tariff/amount at the same provider
                        inside a short window
  * PATIENT_VELOCITY  — one patient claiming at many facilities in days
  * FACILITY_SPIKE    — facility's daily volume z-score vs its own history

Hard rule: this package NEVER denies. Output is a recommendation — CLEAR or
PEND with human-readable reasons — for the adjudicator's review lane. Small
samples abstain (no baseline, no accusation).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime

DEFAULTS = {
    "z_threshold": 3.0,        # amount / volume outliers
    "min_baseline": 8,         # below this, detector abstains
    "velocity_days": 7,
    "velocity_max_providers": 3,
    "duplicate_hours": 24,
}


@dataclass
class Recommendation:
    claim_id: str
    action: str                  # CLEAR | PEND — never DENY
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0           # 0..1 reviewer triage aid
    detail: dict = field(default_factory=dict)


def _day(ts: str) -> str:
    return (ts or "")[:10]


def _dt(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return datetime.min


def _z(value: float, values: list[float]) -> float | None:
    if len(values) < DEFAULTS["min_baseline"]:
        return None
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        # flat baseline: any material excess is still an outlier
        return (value - mean) if value > mean else None
    return (value - mean) / sd


class FraudDetector:
    def __init__(self, config: dict | None = None):
        self.cfg = dict(DEFAULTS, **(config or {}))

    def analyze(self, claim: dict, history: list[dict]) -> Recommendation:
        """claim: the claim under review; history: prior claims (same payer feed)."""
        cid = claim.get("claim_id", "")
        reasons: list[str] = []
        detail: dict = {}
        score = 0.0

        z = self._upcoding(claim, history)
        if z is not None:
            reasons.append(f"UPCODING_Z amount z={z:.1f} vs tariff {claim.get('tariff_code')} baseline")
            detail["upcoding_z"] = round(z, 2)
            score = max(score, min(1.0, z / (self.cfg["z_threshold"] * 2)))

        dup = self._duplicate(claim, history)
        if dup:
            reasons.append(f"DUPLICATE_CLAIM matches {dup} within {self.cfg['duplicate_hours']}h")
            detail["duplicate_of"] = dup
            score = max(score, 0.9)

        vel = self._velocity(claim, history)
        if vel:
            providers = sorted(vel)
            reasons.append(f"PATIENT_VELOCITY {len(providers)} providers in {self.cfg['velocity_days']}d: {', '.join(providers)}")
            detail["velocity_providers"] = providers
            score = max(score, 0.7)

        spike = self._facility_spike(claim, history)
        if spike is not None:
            reasons.append(f"FACILITY_SPIKE daily volume z={spike:.1f} for {claim.get('provider_id')}")
            detail["facility_volume_z"] = round(spike, 2)
            score = max(score, min(1.0, spike / (self.cfg["z_threshold"] * 2)))

        return Recommendation(cid, "PEND" if reasons else "CLEAR", reasons,
                              round(score, 2), detail)

    # ------------------------------------------------------------- detectors
    def _upcoding(self, claim: dict, history: list[dict]) -> float | None:
        amount = claim.get("amount_ngn")
        tariff = claim.get("tariff_code")
        if not isinstance(amount, (int, float)) or not tariff:
            return None
        baseline = [h["amount_ngn"] for h in history
                    if h.get("tariff_code") == tariff
                    and isinstance(h.get("amount_ngn"), (int, float))
                    and h.get("claim_id") != claim.get("claim_id")]
        z = _z(amount, baseline)
        return z if z is not None and z >= self.cfg["z_threshold"] else None

    def _duplicate(self, claim: dict, history: list[dict]) -> str | None:
        when = _dt(claim.get("ts", ""))
        for h in history:
            if h.get("claim_id") == claim.get("claim_id"):
                continue
            same = (h.get("patient_id") == claim.get("patient_id")
                    and h.get("provider_id") == claim.get("provider_id")
                    and h.get("tariff_code") == claim.get("tariff_code")
                    and h.get("amount_ngn") == claim.get("amount_ngn"))
            if not same:
                continue
            delta_h = abs((_dt(h.get("ts", "")) - when).total_seconds()) / 3600
            if delta_h <= self.cfg["duplicate_hours"]:
                return h.get("claim_id", "?")
        return None

    def _velocity(self, claim: dict, history: list[dict]) -> set[str] | None:
        pid = claim.get("patient_id")
        if not pid:
            return None
        when = _dt(claim.get("ts", ""))
        providers = {claim.get("provider_id", "")}
        days = self.cfg["velocity_days"]
        for h in history:
            if h.get("patient_id") != pid:
                continue
            if abs((_dt(h.get("ts", "")) - when).days) <= days:
                providers.add(h.get("provider_id", ""))
        providers.discard("")
        return providers if len(providers) > self.cfg["velocity_max_providers"] else None

    def _facility_spike(self, claim: dict, history: list[dict]) -> float | None:
        provider = claim.get("provider_id")
        day = _day(claim.get("ts", ""))
        if not provider or not day:
            return None
        by_day: dict[str, int] = {}
        for h in history:
            if h.get("provider_id") == provider:
                d = _day(h.get("ts", ""))
                if d and d != day:
                    by_day[d] = by_day.get(d, 0) + 1
        today = sum(1 for h in history
                    if h.get("provider_id") == provider and _day(h.get("ts", "")) == day) + 1
        z = _z(today, list(by_day.values()))
        return z if z is not None and z >= self.cfg["z_threshold"] else None
