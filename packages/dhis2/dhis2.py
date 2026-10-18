#!/usr/bin/env python3
"""DHIS2 exporter — the platform speaks the ministry's language
(ADR-0021).

Nigeria's HMIS runs on DHIS2. A platform that cannot feed it makes the
state run two registries forever. This package converts what OpenHealth
already computes — per-facility MeasureReports (packages/quality) and
k-anonymous surveillance aggregates (packages/surveillance) — into
DHIS2 dataValueSet payloads, ready to POST to /api/dataValueSets or to
spool to disk for the monthly USB run to the state office.

Design rules:
  - mapping is data: ORG_UNITS and DATA_ELEMENTS map platform ids to
    DHIS2 UIDs; unmapped ids are reported, never silently dropped —
    a number the ministry cannot trace is worse than no number;
  - counts only, always: what leaves here is what already left the
    facility anonymized (ADR-0020), DHIS2 changes nothing about that;
  - offline-first: export_batch writes the payload AND a spool file;
    the sync tier can replay spools in order.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Mapping:
    """Platform id -> DHIS2 uid. Loaded from JSON so a state edits
    configuration, not code."""
    org_units: dict[str, str] = field(default_factory=dict)      # facility -> OU uid
    data_elements: dict[str, str] = field(default_factory=dict)  # measure/pop -> DE uid

    @classmethod
    def load(cls, path: str) -> "Mapping":
        with open(path) as f:
            doc = json.load(f)
        return cls(org_units=doc.get("org_units", {}),
                   data_elements=doc.get("data_elements", {}))


def period_of(measure_report: dict) -> str:
    """DHIS2 monthly period YYYYMM from the report period start."""
    start = measure_report.get("period", {}).get("start", "")
    return start[:4] + start[5:7] if len(start) >= 7 else ""


def measure_report_to_values(report: dict, mapping: Mapping) -> tuple[list[dict], list[str]]:
    """One MeasureReport -> (dataValues, warnings). Each population
    count maps to a data element named '<measure>_<population>'."""
    warnings: list[str] = []
    facility = report.get("subject", {}).get("reference", "").split("/")[-1]
    ou = mapping.org_units.get(facility)
    if not ou:
        return [], [f"facility {facility!r} has no DHIS2 org unit mapping"]
    period = period_of(report)
    if not period:
        return [], [f"report for {facility} has no usable period"]
    measure = report.get("measure", "").rsplit("/", 1)[-1].replace("-", "_")
    values: list[dict] = []
    for group in report.get("group", []):
        for pop in group.get("population", []):
            code = (pop.get("code", {}).get("coding") or [{}])[0].get("code", "")
            de = mapping.data_elements.get(f"{measure}_{code}")
            if not de:
                warnings.append(f"no data element mapping for {measure}_{code}")
                continue
            values.append({"dataElement": de, "period": period, "orgUnit": ou,
                           "value": pop.get("count", 0)})
        score = group.get("measureScore", {}).get("value")
        if score is not None:
            de = mapping.data_elements.get(f"{measure}_rate")
            if de:
                values.append({"dataElement": de, "period": period, "orgUnit": ou,
                               "value": score})
    return values, warnings


def surveillance_to_values(rows: list[dict], mapping: Mapping,
                           area_org_units: dict[str, str] | None = None) -> tuple[list[dict], list[str]]:
    """k-anonymous surveillance aggregates -> dataValues. Suppressed
    cells never export — a suppressed count IS a privacy decision."""
    warnings: list[str] = []
    ous = area_org_units or mapping.org_units
    values: list[dict] = []
    for row in rows:
        if row.get("suppressed") or row.get("count") is None:
            continue
        ou = ous.get(row.get("area", ""))
        de = mapping.data_elements.get(f"surveillance_{row.get('disease')}")
        if not ou or not de:
            warnings.append(f"unmapped surveillance cell {row.get('disease')}@{row.get('area')}")
            continue
        day = str(row.get("day", ""))
        period = day[:4] + day[5:7] if len(day) >= 7 else ""
        values.append({"dataElement": de, "period": period, "orgUnit": ou,
                       "value": row["count"]})
    return values, warnings


def data_value_set(values: list[dict]) -> dict:
    """The DHIS2 /api/dataValueSets payload."""
    return {"dataValues": values}


def export_batch(values: list[dict], spool_path: str | None = None) -> dict:
    """Payload + optional spool append (one JSON object per line — the
    offline USB-run pattern the sync tier already uses)."""
    payload = data_value_set(values)
    if spool_path:
        os.makedirs(os.path.dirname(spool_path) or ".", exist_ok=True)
        with open(spool_path, "a") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")
    return payload
