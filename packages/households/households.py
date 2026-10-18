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
from dataclasses import dataclass, field
from datetime import date, timedelta


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


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
            raise ValueError("invalid coordinates")
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
        }

    def to_geojson(self, anonymize: bool = True, as_of: str = "") -> dict:
        """Planning-map export. anonymize=True: hashed ids, counts only —
        no patient_id, no names, no phones."""
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
        return {"type": "FeatureCollection", "features": features}
