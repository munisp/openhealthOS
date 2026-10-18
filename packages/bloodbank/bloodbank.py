#!/usr/bin/env python3
"""Blood bank — unit-level inventory, compatibility, expiry (ADR-0022).

Every blood unit is typed, dated, and traced: group, product, collection
and expiry, where it is, who it went to. The compatibility rules are
deterministic medicine, not configuration: O- red cells are the
universal donor, AB+ the universal recipient; plasma runs the other way.
Transfusing an incompatible unit kills people — this module refuses it.

Design rules:
  - one record per physical unit, statuses: available -> reserved ->
    transfused | expired | discarded; never deleted (recall audits);
  - compatibility checked at RESERVATION time, again at TRANSFUSION;
  - expiry is a first-class alert: units near expiry surface as a
    worklist, expired units leave the available pool automatically;
  - stdlib, offline-first: the ledger syncs through the usual tier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

PRODUCTS = ("whole-blood", "packed-rbc", "ffp", "platelets", "cryoprecipitate")

GROUPS = ("O-", "O+", "A-", "A+", "B-", "B+", "AB-", "AB+")

# Red-cell / whole-blood compatibility: recipient -> acceptable donors.
# (Plasma compatibility inverts; only red-cell products are the
# transfusion-safety hot path covered here.)
RED_CELL_COMPAT: dict[str, set[str]] = {
    "O-": {"O-"},
    "O+": {"O-", "O+"},
    "A-": {"O-", "A-"},
    "A+": {"O-", "O+", "A-", "A+"},
    "B-": {"O-", "B-"},
    "B+": {"O-", "O+", "B-", "B+"},
    "AB-": {"O-", "A-", "B-", "AB-"},
    "AB+": set(GROUPS),
}

RED_CELL_PRODUCTS = {"whole-blood", "packed-rbc"}

STATUSES = ("available", "reserved", "transfused", "expired", "discarded")


class IncompatibleUnit(Exception):
    pass


class UnitError(Exception):
    pass


@dataclass
class Unit:
    unit_id: str
    group: str
    product: str
    collected: str           # ISO date
    expires: str             # ISO date
    facility: str
    status: str = "available"
    reserved_for: str = ""   # patient ref while reserved
    transfused_to: str = ""  # patient ref after transfusion
    actor: str = ""          # last actor (named-actor doctrine)


def compatible(donor_group: str, product: str, recipient_group: str) -> bool:
    """ABO/Rh compatibility. Red-cell products use the donor matrix;
    plasma products require ABO-identical here (conservative default a
    clinician can override only outside this module)."""
    if product in RED_CELL_PRODUCTS:
        return donor_group in RED_CELL_COMPAT.get(recipient_group, set())
    return donor_group == recipient_group


def _d(iso: str) -> date:
    return date.fromisoformat(iso[:10])


class BloodBank:
    def __init__(self):
        self._units: dict[str, Unit] = {}

    def receive(self, unit: Unit, as_of: str, actor: str) -> None:
        if not unit.unit_id or unit.group not in GROUPS or unit.product not in PRODUCTS:
            raise UnitError("unit_id, valid group and product required")
        if not actor:
            raise UnitError("actor required — inventory changes name who")
        if _d(unit.expires) <= _d(as_of):
            raise UnitError("cannot receive an expired unit")
        if unit.unit_id in self._units:
            raise UnitError("unit already registered — ids are physical labels")
        unit.actor = actor
        self._units[unit.unit_id] = unit

    def get(self, unit_id: str) -> Unit:
        if unit_id not in self._units:
            raise UnitError("unknown unit")
        return self._units[unit_id]

    def available(self, facility: str | None = None, as_of: str | None = None) -> list[Unit]:
        out = []
        for u in self._units.values():
            if u.status != "available":
                continue
            if facility and u.facility != facility:
                continue
            if as_of and _d(u.expires) <= _d(as_of):
                continue  # expired units never show as available
            out.append(u)
        return sorted(out, key=lambda u: u.expires)  # FEFO: first-expiry, first-out

    def reserve(self, unit_id: str, recipient_group: str, patient: str,
                as_of: str, actor: str) -> Unit:
        u = self.get(unit_id)
        if u.status != "available":
            raise UnitError(f"unit is {u.status}, not available")
        if _d(u.expires) <= _d(as_of):
            u.status = "expired"
            raise UnitError("unit expired")
        if not compatible(u.group, u.product, recipient_group):
            raise IncompatibleUnit(
                f"{u.group} {u.product} is incompatible with recipient {recipient_group}")
        u.status, u.reserved_for, u.actor = "reserved", patient, actor
        return u

    def transfuse(self, unit_id: str, recipient_group: str, patient: str,
                  as_of: str, actor: str) -> Unit:
        u = self.get(unit_id)
        if u.status not in ("reserved", "available"):
            raise UnitError(f"unit is {u.status} — cannot transfuse")
        if _d(u.expires) <= _d(as_of):
            u.status = "expired"
            raise UnitError("unit expired at bedside — discard, do not transfuse")
        # bedside re-check: the second verification is the one that saves lives
        if not compatible(u.group, u.product, recipient_group):
            raise IncompatibleUnit(
                f"bedside check failed: {u.group} vs recipient {recipient_group}")
        u.status, u.transfused_to, u.actor = "transfused", patient, actor
        return u

    def discard(self, unit_id: str, reason: str, actor: str) -> Unit:
        if not reason or not actor:
            raise UnitError("discards name why and who")
        u = self.get(unit_id)
        u.status, u.actor = "discarded", actor
        return u

    def expiring_soon(self, as_of: str, within_days: int = 7) -> list[Unit]:
        """The use-it-or-lose-it worklist: available units nearing expiry."""
        horizon = _d(as_of) + timedelta(days=within_days)
        return sorted(
            (u for u in self._units.values()
             if u.status == "available" and _d(as_of) < _d(u.expires) <= horizon),
            key=lambda u: u.expires)

    def sweep_expired(self, as_of: str) -> int:
        """Move expired available units out of the pool. Returns count."""
        n = 0
        for u in self._units.values():
            if u.status == "available" and _d(u.expires) <= _d(as_of):
                u.status = "expired"
                n += 1
        return n

    def stock_by_group(self, facility: str | None = None, as_of: str = "9999-12-31") -> dict[str, int]:
        out = {g: 0 for g in GROUPS}
        for u in self.available(facility, as_of if as_of != "9999-12-31" else None):
            out[u.group] += 1
        return out
