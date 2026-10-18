#!/usr/bin/env python3
"""Staff rota — shifts, coverage requirements, fairness (ADR-0022).

A PHC runs on people showing up. This package turns staffing policy
("two nurses and a CHEW on every day shift, one nurse at night") into
an assignable rota with three guarantees:

  - coverage gaps are VISIBLE: requirements vs actual assignments per
    shift, per cadre — never discovered at 7am;
  - no double-booking: a person cannot hold two shifts at once;
  - fairness is measurable: weekend and night counts per person are
    reported, so the rotas that breed resentment are caught in review.

Auto-fill is a simple fair greedy: least-assigned eligible person for
each uncovered slot, ties broken by fewest weekend/night shifts.
Deterministic — same inputs, same rota, no clock, no randomness.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SHIFT_KINDS = ("day", "night", "weekend-day", "weekend-night")


class RotaError(Exception):
    pass


@dataclass
class Staff:
    staff_id: str
    name: str
    cadre: str            # nurse | CHEW | physician | midwife | ...
    max_shifts_per_week: int = 5


@dataclass
class Shift:
    shift_id: str
    day: str              # ISO date
    kind: str             # day | night | weekend-day | weekend-night
    requires: dict        # cadre -> count needed
    assigned: list = field(default_factory=list)  # staff_ids


@dataclass
class Assignment:
    shift_id: str
    staff_id: str


class Rota:
    def __init__(self):
        self.staff: dict[str, Staff] = {}
        self.shifts: dict[str, Shift] = {}

    def add_staff(self, s: Staff) -> None:
        if not s.staff_id or not s.cadre:
            raise RotaError("staff_id and cadre required")
        self.staff[s.staff_id] = s

    def add_shift(self, s: Shift) -> None:
        if s.kind not in SHIFT_KINDS:
            raise RotaError(f"kind must be one of {SHIFT_KINDS}")
        for cadre, n in s.requires.items():
            if n < 1:
                raise RotaError("requirements must be positive")
        self.shifts[s.shift_id] = s

    def assign(self, shift_id: str, staff_id: str) -> Assignment:
        if staff_id not in self.staff:
            raise RotaError("unknown staff")
        sh = self.shifts.get(shift_id)
        if not sh:
            raise RotaError("unknown shift")
        person = self.staff[staff_id]
        if staff_id in sh.assigned:
            raise RotaError("already assigned to this shift")
        # no double-booking the same day/night window
        for other in self.shifts.values():
            if other.shift_id != shift_id and other.day == sh.day and \
               other.kind == sh.kind and staff_id in other.assigned:
                raise RotaError(f"{person.name} already works {sh.kind} on {sh.day}")
        # weekly cap (rota covers one week by convention)
        week_load = sum(1 for o in self.shifts.values() if staff_id in o.assigned)
        if week_load >= person.max_shifts_per_week:
            raise RotaError(f"{person.name} is at their weekly cap")
        sh.assigned.append(staff_id)
        return Assignment(shift_id, staff_id)

    def coverage(self) -> list[dict]:
        """Gap report per shift: required vs assigned per cadre."""
        out = []
        for sh in sorted(self.shifts.values(), key=lambda s: (s.day, s.kind)):
            row = {"shift_id": sh.shift_id, "day": sh.day, "kind": sh.kind,
                   "gaps": {}, "ok": True}
            for cadre, need in sh.requires.items():
                have = sum(1 for sid in sh.assigned
                           if self.staff[sid].cadre == cadre)
                if have < need:
                    row["gaps"][cadre] = need - have
                    row["ok"] = False
            out.append(row)
        return out

    def fairness(self) -> dict:
        """Weekend + night counts per person — resentment made visible."""
        out = {}
        for sid, person in self.staff.items():
            nights = weekends = total = 0
            for sh in self.shifts.values():
                if sid in sh.assigned:
                    total += 1
                    nights += "night" in sh.kind
                    weekends += "weekend" in sh.kind
            out[sid] = {"name": person.name, "total": total,
                        "nights": nights, "weekends": weekends}
        return out

    def autofill(self) -> list[Assignment]:
        """Fair greedy: least-loaded eligible person per uncovered slot."""
        made: list[Assignment] = []
        for row in self.coverage():
            if row["ok"]:
                continue
            sh = self.shifts[row["shift_id"]]
            for cadre, gap in row["gaps"].items():
                for _ in range(gap):
                    candidates = [p for p in self.staff.values()
                                  if p.cadre == cadre and p.staff_id not in sh.assigned]
                    def load(p):
                        f = self.fairness()[p.staff_id]
                        return (f["total"], f["weekends"] + f["nights"], p.staff_id)
                    candidates.sort(key=load)
                    placed = False
                    for c in candidates:
                        try:
                            made.append(self.assign(sh.shift_id, c.staff_id))
                            placed = True
                            break
                        except RotaError:
                            continue
                    if not placed:
                        break  # gap stays visible in the next coverage() call
        return made
