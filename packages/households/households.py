#!/usr/bin/env python3
"""Household GIS registers — CHW catchment, defaulter tracing, GeoJSON
(ADR-0013, innovation).

PHC in Nigeria is organised around wards and catchments, not hospital
catchment areas. This package is the CHW household register: who lives
where, which children are due for immunization, which households are
outside the facility's walking radius.

Privacy split is deliberate:
  * defaulters() carries patient_id — for the CHW's own worklist
  * to_geojson() anonymizes by default (hashed ids, no names/phones) — for
    planning maps and DHIS2-style aggregate export
"""
from __future__ import annotations

import hashlib
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

# Geodesy is owned by geolock (ADR-0075): one contract, one function.
# Exports are owned by gisexport (ADR-0076): every GeoJSON artifact
# flows through the governed exporter — this package was the estate's
# last raw-FeatureCollection site. Same in-repo sys.path pattern as
# agentwrite — stdlib only.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "geolock"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "gisexport"))
import geolock  # noqa: E402
import gisexport  # noqa: E402

HOUSEHOLDS_VERSION = "households@1.2.0"

#: Coordinates are rounded to 5 decimals before export (~1.1 m at the
#: equator): planning-map precision, not household-doorstep precision.
#: Now stated on the artifact — a privacy choice is data, not silence.
COORDINATE_QUANTIZATION = ("5 decimal places (~1.1 m at the equator) — "
                           "planning-map precision, stated")

#: Stated basis carried on every coverage() result — absence is not a
#: value; the arithmetic a catchment decision rests on is disclosed.
GEODESY_BASIS = "geolock-geodesy-contract"


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle km — delegates to geolock.distance_km (ADR-0075).
    Kept under its historic name: chw-tasks and malariaprog import this
    symbol. Out-of-range degrees now refuse (fail-closed) instead of
    silently measuring nonsense."""
    return geolock.distance_km(lat1, lon1, lat2, lon2)


@dataclass
class Member:
    patient_id: str
    next_due: str = ""           # ISO date of next due service (immunization, ANC…)
    due_service: str = ""        # e.g. "measles-2", "anc-visit-3"
    name: str = ""               # stays in the register; never in GeoJSON


@dataclass
class Defaulter:
    patient_id: str
    household_id: str
    area: str
    due_service: str
    due_date: str
    overdue_days: int
    lat: float
    lon: float


@dataclass
class Household:
    household_id: str
    area: str                    # ward code
    lat: float
    lon: float
    members: list[Member] = field(default_factory=list)


class HouseholdRegister:
    def __init__(self):
        self._households: dict[str, Household] = {}

    def register(self, household: Household) -> None:
        if not household.household_id:
            raise ValueError("household_id required")
        if not (-90 <= household.lat <= 90 and -180 <= household.lon <= 180):
            raise ValueError(
                "invalid coordinates: lat=%r lon=%r — degrees are WGS-84 "
                "lat/lng; metre-space coordinates are not degrees, "
                "reproject first (geolock.transform)"
                % (household.lat, household.lon))
        self._households[household.household_id] = household

    def add_member(self, household_id: str, member: Member) -> None:
        self._households[household_id].members.append(member)

    def get(self, household_id: str) -> Household | None:
        return self._households.get(household_id)

    def households(self) -> list[Household]:
        return list(self._households.values())

    def defaulters(self, as_of: str, grace_days: int = 0) -> list[Defaulter]:
        """Overdue-for-service worklist, oldest first."""
        today = _d(as_of) - timedelta(days=grace_days)
        out: list[Defaulter] = []
        for h in self._households.values():
            for m in h.members:
                if not m.next_due:
                    continue
                due = _d(m.next_due)
                if due < today:
                    out.append(Defaulter(m.patient_id, h.household_id, h.area,
                                         m.due_service, m.next_due,
                                         (today - due).days, h.lat, h.lon))
        return sorted(out, key=lambda d: d.overdue_days, reverse=True)

    def coverage(self, facility_lat: float, facility_lon: float,
                 radius_km: float) -> dict:
        """Catchment coverage: who is inside/outside the service radius."""
        inside, outside = [], []
        for h in self._households.values():
            km = haversine_km(facility_lat, facility_lon, h.lat, h.lon)
            (inside if km <= radius_km else outside).append((h, km))
        return {
            "radius_km": radius_km,
            "households_inside": len(inside),
            "households_outside": len(outside),
            "members_inside": sum(len(h.members) for h, _ in inside),
            "members_outside": sum(len(h.members) for h, _ in outside),
            "furthest_km": round(max((km for _, km in inside), default=0.0), 2),
            "outside_ids": [h.household_id for h, _ in outside],
            "geodesy": {"basis": GEODESY_BASIS,
                        "contract": geolock.GEODESY_CONTRACT},
        }

    def to_geojson(self, anonymize: bool = True, as_of: str = "",
                   *, registry=None) -> dict:
        """Planning-map export via the governed exporter (ADR-0076).
        anonymize=True: hashed ids, counts only — no patient_id, no
        names, no phones. The privacy choice and the coordinate
        quantization travel ON the artifact as data; an identified
        export (anonymize=False) says so loudly. With a geolock
        registry injected the collection is registry-governed; without
        one the RFC 7946 default basis is stated, not assumed."""
        defaulter_count: dict[str, int] = {}
        if as_of:
            for d in self.defaulters(as_of):
                defaulter_count[d.household_id] = defaulter_count.get(d.household_id, 0) + 1
        features = []
        for h in self._households.values():
            hid = (hashlib.sha256(h.household_id.encode()).hexdigest()[:12]
                   if anonymize else h.household_id)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point",
                             "coordinates": [round(h.lon, 5), round(h.lat, 5)]},
                "properties": {"household": hid, "area": h.area,
                               "members": len(h.members),
                               "defaulters": defaulter_count.get(h.household_id, 0)},
            })
        fc = gisexport.collection(features, registry=registry)
        fc["openhealth:households"] = {
            "anonymized": anonymize,
            "coordinate_quantization": COORDINATE_QUANTIZATION,
            "as_of": as_of or None,
            "basis": "privacy choices are data — an identified export "
                     "says so on the artifact"}
        return fc
