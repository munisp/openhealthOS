#!/usr/bin/env python3
"""Quality measures engine — the same standard of care, everywhere
(ADR-0020, FHIR-IG-native).

A care standard that lives in a PDF does not survive contact with 774
LGAs. This package makes the standard executable: measures are FHIR
Measure resources (JSON, versioned in fhir/measures/), evaluation runs
over the SHR bundle exports facilities already produce, and the output
is a FHIR MeasureReport per facility per period — comparable by
construction, because every facility answers the same question against
the same definition.

Design rules:
  - measures are data, not code: a program officer edits a JSON file,
    not a deployment;
  - evaluation is a small, auditable rule engine: numerator and
    denominator are criteria over FHIR resources — every number can be
    traced back to the exact resources that counted;
  - proportion measures report rate = numerator / denominator with the
    populations alongside, never a bare percentage;
  - stdlib only; offline-first; deterministic (no clock, no randomness).

This module implements a pragmatic subset of FHIR Measure evaluation
(proportion measures over Bundle contents). It does not implement CQL —
criteria are declarative path/op/value tuples, which is what the bundled
Nigerian priority measures need.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable

# ---------------------------------------------------------------------
# Criteria engine
# ---------------------------------------------------------------------

OPS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "in": lambda a, b: a in b,
    "contains": lambda a, b: isinstance(a, (list, str)) and b in a,
    "exists": lambda a, b: (a is not None) == bool(b),
}


def get_path(resource: dict, path: str) -> Any:
    """Dotted path into a FHIR resource; returns the first coded value
    for CodeableConcept-shaped nodes (`.code` suffix is implied)."""
    node: Any = resource
    for part in path.split("."):
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    if isinstance(node, list) and node and isinstance(node[0], dict):
        # CodeableConcept: [{coding: [{code}]}] or [{code}]
        first = node[0]
        if "coding" in first:
            coding = first["coding"]
            return coding[0].get("code") if coding else None
        return first.get("code")
    if isinstance(node, dict) and "coding" in node:  # single CodeableConcept
        coding = node["coding"]
        return coding[0].get("code") if coding else None
    if isinstance(node, dict) and "value" in node and len(node) <= 2:
        return node.get("value")
    return node


def matches(resource: dict, criteria: list[dict]) -> bool:
    for c in criteria:
        op = OPS[c.get("op", "eq")]
        if not op(get_path(resource, c["path"]), c.get("value")):
            return False
    return True


def in_period(resource: dict, period_start: str, period_end: str,
              date_paths: Iterable[str] = ("period.start", "effectiveDateTime",
                                           "occurrenceDateTime", "recordedDate", "date",
                                           "authoredOn", "start", "end")) -> bool:
    for p in date_paths:
        v = get_path(resource, p)
        if isinstance(v, str) and v[:10] >= period_start and v[:10] <= period_end:
            return True
    return False


# ---------------------------------------------------------------------
# Measure evaluation
# ---------------------------------------------------------------------

@dataclass
class Population:
    code: str            # initial-population | denominator | numerator
    resource_type: str
    criteria: list[dict] = field(default_factory=list)
    count: int = 0
    members: list[str] = field(default_factory=list)  # resource ids (audit)
    subject_basis: bool = False  # count distinct patients, not resources


@dataclass
class EvaluatedMeasure:
    measure_url: str
    measure_name: str
    title: str
    facility: str
    period_start: str
    period_end: str
    populations: list[Population]

    @property
    def rate(self) -> float | None:
        num = next((p.count for p in self.populations if p.code == "numerator"), 0)
        den = next((p.count for p in self.populations if p.code == "denominator"), 0)
        return (num / den) if den else None

    def to_measure_report(self) -> dict:
        """A FHIR R4 MeasureReport (type=data-exchange, subject=facility)."""
        groups = [{
            "population": [{
                "code": {"coding": [{"system":
                                     "http://terminology.hl7.org/CodeSystem/measure-population",
                                     "code": p.code}]},
                "count": p.count,
            } for p in self.populations],
            "measureScore": {"value": round(self.rate, 4)} if self.rate is not None
                            else {"extension": [{"url": "http://hl7.org/fhir/StructureDefinition/data-absent-reason",
                                                 "valueCode": "unknown"}]},
        }]
        return {
            "resourceType": "MeasureReport",
            "status": "complete",
            "type": "summary",
            "measure": self.measure_url,
            "period": {"start": self.period_start, "end": self.period_end},
            "reporter": {"display": "OpenHealth OS quality engine"},
            "subject": {"reference": f"Location/{self.facility}"},
            "group": groups,
        }


def load_measure(doc: dict) -> dict:
    """Validate the pragmatic subset: a Measure with one group whose
    populations carry `extension` criteria (our JSON DSL)."""
    assert doc.get("resourceType") == "Measure", "not a Measure resource"
    groups = doc.get("group")
    assert groups and groups[0].get("population"), "Measure needs one group with populations"
    return doc


def evaluate(measure: dict, bundle: list[dict], facility: str,
             period_start: str, period_end: str) -> EvaluatedMeasure:
    """Evaluate one measure over the resources of ONE facility."""
    load_measure(measure)
    by_type: dict[str, list[dict]] = {}
    for r in bundle:
        if not isinstance(r, dict) or "resourceType" not in r:
            continue
        # facility scoping: resources carry `meta.tag` facility or a
        # serviceProvider/managingOrganization/location reference
        if not _belongs_to(r, facility):
            continue
        by_type.setdefault(r["resourceType"], []).append(r)

    pops: list[Population] = []
    for pop in measure["group"][0]["population"]:
        code = pop["code"]["coding"][0]["code"]
        spec = _population_spec(pop)
        p = Population(code=code, resource_type=spec["resourceType"],
                       criteria=spec.get("criteria", []),
                       subject_basis=spec.get("subject_basis", False))
        for r in by_type.get(spec["resourceType"], []):
            if spec.get("in_period", True) and not in_period(r, period_start, period_end):
                continue
            if matches(r, p.criteria):
                rid = r.get("id", "?")
                if rid not in p.members:  # idempotent over duplicates
                    p.members.append(rid)
                    p.count += 1
        pops.append(p)

    # per-patient measures: numerator must be a subset of denominator's
    # patients — evaluate per subject, not per resource count
    if measure.get("scoring", {}).get("coding", [{}])[0].get("code") == "proportion":
        pops = _reconcile_per_subject(pops, by_type, facility,
                                      period_start, period_end)
    return EvaluatedMeasure(
        measure_url=measure.get("url", measure.get("name", "unknown")),
        measure_name=measure.get("name", "unknown"),
        title=measure.get("title", measure.get("name", "")),
        facility=facility, period_start=period_start, period_end=period_end,
        populations=pops)


def _population_spec(pop: dict) -> dict:
    for ext in pop.get("extension", []):
        if ext.get("url", "").endswith("openhealth-population-criteria"):
            return ext["value"]
    return {"resourceType": "Encounter"}


def _belongs_to(resource: dict, facility: str) -> bool:
    for path in ("serviceProvider.reference", "managingOrganization.reference",
                 "location.reference", "meta.tag.code"):
        v = get_path(resource, path)
        if isinstance(v, str) and facility in v:
            return True
    return False


def _subject(resource: dict) -> str | None:
    for path in ("subject.reference", "patient.reference"):
        v = get_path(resource, path)
        if isinstance(v, str):
            return v
    return None


def _reconcile_per_subject(pops: list[Population], by_type, facility,
                           period_start, period_end) -> list[Population]:
    """For proportion measures with distinct-patient semantics: count a
    patient once in the denominator, once in the numerator when they have
    the qualifying activity. Measures opt in via population
    `subject_basis: true` in the criteria spec."""
    if not any(p.subject_basis for p in pops):
        return pops
    den = next((p for p in pops if p.code == "denominator"), None)
    num = next((p for p in pops if p.code == "numerator"), None)
    if not den or not num:
        return pops
    den_subjects = set()
    num_subjects = set()
    for r in by_type.get(den.resource_type, []):
        if in_period(r, period_start, period_end) and matches(r, den.criteria):
            s = _subject(r)
            if s:
                den_subjects.add(s)
    for r in by_type.get(num.resource_type, []):
        if in_period(r, period_start, period_end) and matches(r, num.criteria):
            s = _subject(r)
            if s and s in den_subjects:
                num_subjects.add(s)
    den.count, den.members = len(den_subjects), sorted(den_subjects)
    num.count, num.members = len(num_subjects), sorted(num_subjects)
    return pops


# ---------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------

def evaluate_library(measures: list[dict], bundles_by_facility: dict[str, list[dict]],
                     period_start: str, period_end: str) -> list[EvaluatedMeasure]:
    """Every measure x every facility: the comparability grid."""
    out: list[EvaluatedMeasure] = []
    for m in measures:
        for fac, bundle in sorted(bundles_by_facility.items()):
            out.append(evaluate(m, bundle, fac, period_start, period_end))
    return out


def scorecard(results: list[EvaluatedMeasure]) -> dict:
    """Facility x measure grid with rates — the LGA officer's view.
    Standard care made visible: same definition, every location."""
    grid: dict[str, dict[str, Any]] = {}
    for r in results:
        cell = {"rate": r.rate,
                "populations": {p.code: p.count for p in r.populations}}
        grid.setdefault(r.facility, {})[r.measure_name] = cell
    return {"period": {"start": results[0].period_start if results else "",
                       "end": results[0].period_end if results else ""},
            "facilities": grid}


def load_measure_file(path: str) -> dict:
    with open(path) as f:
        return load_measure(json.load(f))
