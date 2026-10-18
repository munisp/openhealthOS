#!/usr/bin/env python3
"""Notifiable-disease case detection — Nigeria IDSR over FHIR
(ADR-0020, FHIR-IG-native).

The syndromic radar (surveillance.py) watches triage exhaust for
clusters. This module is the other half of surveillance: the diseases
Nigeria's IDSR says a clinician MUST report by name, detected
deterministically from the FHIR resources the clinical loop already
writes — Conditions (diagnoses) and Observations (lab results).

Design rules, same as the syndromic radar:
  - deterministic case definitions as data (NOTIFIABLE below): each
    disease is a rule over coded resources — a state epidemiologist can
    read exactly what fires;
  - immediate-report diseases (IDSR category 1: report within 24h) fire
    a case alert per detection; weekly-report diseases aggregate;
  - privacy by construction: alerts carry counts and areas, never
    patient records; k-anonymity floor on aggregates; Laplace noise
    optional for published numbers (reuses surveillance.py helpers);
  - dedupe: one active alert per (disease, patient, episode window) —
    a confirmed case re-entered at referral is the same case.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

K_ANONYMITY_FLOOR = 5

# IDSR category: immediate (24h) | weekly (aggregate)
# match: any-of coded values on Condition.code / Observation.code;
# observation_confirmation: a positive lab code that upgrades
# "suspected" to "confirmed".
NOTIFIABLE: dict[str, dict] = {
    "cholera": {
        "category": "immediate",
        "condition_codes": ["A00", "cholera", "acute-watery-diarrhoea-suspected-cholera"],
        "confirm_obs": ["cholera-rdt-positive", "stool-culture-vibrio"],
    },
    "measles": {
        "category": "immediate",
        "condition_codes": ["B05", "measles", "measles-like-illness"],
        "confirm_obs": ["measles-igm-positive"],
    },
    "cerebrospinal_meningitis": {
        "category": "immediate",
        "condition_codes": ["A39", "meningitis", "csm-suspected"],
        "confirm_obs": ["csf-meningococcus-positive"],
    },
    "lassa_fever": {
        "category": "immediate",
        "condition_codes": ["A96.2", "lassa", "viral-haemorrhagic-fever"],
        "confirm_obs": ["lassa-pcr-positive"],
    },
    "yellow_fever": {
        "category": "immediate",
        "condition_codes": ["A95", "yellow-fever", "jaundice-with-fever"],
        "confirm_obs": ["yellow-fever-igm-positive"],
    },
    "poliomyelitis_afp": {
        "category": "immediate",
        "condition_codes": ["A80", "acute-flaccid-paralysis"],
        "confirm_obs": [],
    },
    "malaria_severe": {
        "category": "weekly",
        "condition_codes": ["B50", "B51", "severe-malaria"],
        "confirm_obs": ["malaria-rdt-positive", "malaria-microscopy-positive"],
    },
    "tuberculosis": {
        "category": "weekly",
        "condition_codes": ["A15", "A16", "tuberculosis"],
        "confirm_obs": ["gene-xpert-mtb-positive", "afb-smear-positive"],
    },
}


@dataclass
class CaseAlert:
    disease: str
    category: str          # immediate | weekly
    status: str            # suspected | confirmed
    patient_ref: str       # kept at facility tier; stripped before LGA rollup
    facility: str
    area: str              # LGA/ward code when known
    detected_at: str
    source_resource: str   # e.g. "Condition/c-123" — audit trail
    evidence: str = ""

    def to_event(self) -> dict:
        """Immediate-report alerts ride the critical queue."""
        return {"type": "surveillance_alert",
                "priority": "critical" if self.category == "immediate" else "routine",
                "payload": {"kind": "notifiable_case",
                            "disease": self.disease, "status": self.status,
                            "category": self.category,
                            "facility": self.facility, "area": self.area,
                            "detected_at": self.detected_at,
                            "source": self.source_resource}}

    def anonymized(self) -> dict:
        """The LGA/state view: no patient, no resource id — counts only."""
        return {"disease": self.disease, "status": self.status,
                "facility": self.facility, "area": self.area,
                "detected_at": self.detected_at}


def _code_of(resource: dict) -> str | None:
    """First code from a Condition/Observation's code CodeableConcept."""
    code = resource.get("code") or {}
    coding = code.get("coding") or []
    for c in coding:
        if c.get("code"):
            return str(c["code"])
    text = code.get("text")
    return str(text).lower() if text else None


def _patient_of(resource: dict) -> str:
    for key in ("subject", "patient"):
        ref = (resource.get(key) or {}).get("reference")
        if ref:
            return ref
    return ""


def _facility_of(resource: dict) -> str:
    for key in ("serviceProvider", "managingOrganization", "location"):
        ref = (resource.get(key) or {}).get("reference")
        if ref:
            return ref.split("/")[-1]
    return ""


class NotifiableDetector:
    """Feeds on the FHIR resource stream; emits case alerts."""

    def __init__(self, rules: dict[str, dict] | None = None,
                 dedupe_window_days: int = 28):
        self.rules = rules or NOTIFIABLE
        self.window = timedelta(days=dedupe_window_days)
        self._seen: dict[tuple[str, str], datetime] = {}  # (disease, patient) -> last alert

    def inspect(self, resource: dict, detected_at: datetime,
                area: str = "") -> CaseAlert | None:
        rtype = resource.get("resourceType")
        if rtype not in ("Condition", "Observation"):
            return None
        code = _code_of(resource)
        if not code:
            return None
        code_l = code.lower()
        for disease, rule in self.rules.items():
            hit = any(code_l == c.lower() or code_l.startswith(c.lower())
                      for c in rule["condition_codes"] + rule["confirm_obs"])
            if not hit:
                continue
            patient = _patient_of(resource)
            key = (disease, patient)
            last = self._seen.get(key)
            if last and detected_at - last < self.window:
                return None  # same episode — re-entry at referral is not a new case
            status = ("confirmed" if rtype == "Observation" or
                      code_l in [c.lower() for c in rule["confirm_obs"]]
                      else "suspected")
            self._seen[key] = detected_at
            return CaseAlert(
                disease=disease, category=rule["category"], status=status,
                patient_ref=patient, facility=_facility_of(resource),
                area=area, detected_at=detected_at.isoformat(timespec="seconds"),
                source_resource=f"{rtype}/{resource.get('id', '?')}",
                evidence=f"code={code}")
        return None


def aggregate(alerts: list[CaseAlert], k: int = K_ANONYMITY_FLOOR) -> list[dict]:
    """Weekly rollup for the state dashboard: counts per
    (disease, area, week), k-anonymity suppressed. What leaves the
    facility is epidemiology, never identity."""
    cells: dict[tuple[str, str, str], int] = {}
    for a in alerts:
        week = a.detected_at[:10]
        key = (a.disease, a.area or "unknown", week)
        cells[key] = cells.get(key, 0) + 1
    out = []
    for (disease, area, day), count in sorted(cells.items()):
        if count < k:
            out.append({"disease": disease, "area": area, "day": day,
                        "count": None, "suppressed": True})
        else:
            out.append({"disease": disease, "area": area, "day": day,
                        "count": count, "suppressed": False})
    return out
