"""Chronic disease registries (ADR-0058): HIV cascade, hypertension/diabetes
cohorts. Tracks care-continuum stages and medication adherence; feeds
pophealth cohorts and payrails quality adjustments. Fail-soft: a patient can
be in multiple registries; stage transitions are evidence-gated.
"""
from __future__ import annotations
import json, sys
from datetime import date

REGISTRIES = {
    "hiv": ["diagnosed", "linked_to_care", "on_art", "retained", "virally_suppressed"],
    "hypertension": ["diagnosed", "on_treatment", "controlled"],
    "diabetes": ["diagnosed", "on_treatment", "hba1c_controlled"],
}

class RegistryError(Exception):
    pass

def adherence_score(doses_taken: int, doses_expected: int) -> float:
    if doses_expected <= 0:
        return 0.0
    return round(min(doses_taken / doses_expected, 1.0), 3)

def adherence_band(score: float) -> str:
    if score >= 0.95: return "high"
    if score >= 0.80: return "moderate"
    return "low"

class ChronicRegistry:
    def __init__(self):
        self.members = {}  # (registry, patient_id) -> record

    def enrol(self, registry: str, patient_id: str, enrolled: str):
        if registry not in REGISTRIES:
            raise RegistryError(f"unknown registry {registry}")
        key = (registry, patient_id)
        if key in self.members:
            raise RegistryError("already enrolled")
        self.members[key] = {"registry": registry, "patient_id": patient_id,
                             "stage": REGISTRIES[registry][0], "enrolled": enrolled,
                             "history": [(enrolled, REGISTRIES[registry][0])]}
        return self.members[key]

    def advance(self, registry: str, patient_id: str, evidence: str, on: str):
        r = self.members[(registry, patient_id)]
        stages = REGISTRIES[registry]
        nxt = stages.index(r["stage"]) + 1
        if nxt >= len(stages):
            raise RegistryError("already at final stage")
        if not evidence:
            raise RegistryError("stage transitions require evidence (lab ref, visit id)")
        r["stage"] = stages[nxt]
        r["history"].append((on, stages[nxt]))
        return r

    def cascade_counts(self, registry: str) -> dict:
        counts = {s: 0 for s in REGISTRIES[registry]}
        for (reg, _), r in self.members.items():
            if reg == registry:
                counts[r["stage"]] += 1
        return counts

def hiv_cascade_report(members: list[dict]) -> dict:
    stages = REGISTRIES["hiv"]
    n = {s: sum(1 for m in members if m.get("stage") == s) for s in stages}
    diagnosed = max(sum(n.values()), 1)
    return {"stages": n,
            "pct_on_art": round(100 * (n["on_art"] + n["retained"] + n["virally_suppressed"]) / diagnosed, 1),
            "pct_suppressed": round(100 * n["virally_suppressed"] / diagnosed, 1)}

def main(argv):
    doc = json.load(sys.stdin)
    print(json.dumps(hiv_cascade_report(doc.get("members", [])), indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
