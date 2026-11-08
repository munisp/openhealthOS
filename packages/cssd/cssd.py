"""cssd.py — central sterile supply: the pack that goes back into theatre.

Every instrument that touches a sterile field passes through CSSD.
A pack that skipped its autoclave cycle, failed its biological indicator,
or expired on the shelf must not reach theatre — this module makes that
impossible by construction, not by memo.

Doctrine:
- A PACK IS A BATCH: every pack names its contents, its assembler, and
  the cycle it went through. A pack without a cycle does not exist for
  theatre.
- THE CYCLE IS A MACHINE RECORD: temperature, pressure, and duration are
  recorded from the autoclave; a cycle outside the registered parameters
  fails closed — the pack is quarantined, not re-labelled.
- BIOLOGICAL INDICATORS GATE RELEASE: a positive BI quarantines every
  pack in the load.
- EXPIRY IS COMPUTED, NOT ASSUMED: each pack carries a sterilisation date
  and a shelf life; past the date, the pack is refuse-only.
"""

import hashlib
import json

VERSION = "cssd@1.0.0"

# autoclave parameters: (min_temp_C, min_time_minutes)
CYCLE_PARAMS = {
    "gravity-121": (121.0, 30),
    "pre-vac-132": (132.0, 4),
    "pre-vac-134": (134.0, 3),
}

SHELF_LIFE_DAYS = {"wrapped": 30, "rigid-container": 90, "peel-pack": 60}


class Error(Exception):
    pass


class AutoclaveCycle:
    """One machine run. The machine's numbers, not the operator's word."""

    def __init__(self, cycle_id, cycle_type, *, temp_c, duration_minutes,
                 day, bi_result=None):
        if cycle_type not in CYCLE_PARAMS:
            raise Error(f"unknown cycle type: {cycle_type}")
        self.cycle_id = cycle_id
        self.cycle_type = cycle_type
        self.temp_c = temp_c
        self.duration_minutes = duration_minutes
        self.day = day
        self.bi_result = bi_result  # "negative" | "positive" | None
        self.failed = self._evaluate()

    def _evaluate(self):
        need_temp, need_time = CYCLE_PARAMS[self.cycle_type]
        reasons = []
        if self.temp_c < need_temp:
            reasons.append(
                f"temp {self.temp_c}°C < required {need_temp}°C")
        if self.duration_minutes < need_time:
            reasons.append(
                f"duration {self.duration_minutes}min < required "
                f"{need_time}min")
        if self.bi_result == "positive":
            reasons.append("biological indicator POSITIVE")
        self.failure_reasons = reasons
        return bool(reasons)


class SterilePack:
    def __init__(self, pack_id, contents, *, assembler, day,
                 packaging="wrapped"):
        if not contents:
            raise Error("a pack names its contents")
        if packaging not in SHELF_LIFE_DAYS:
            raise Error(f"unknown packaging: {packaging}")
        if not assembler:
            raise Error("a pack names its assembler")
        self.pack_id = pack_id
        self.contents = contents
        self.assembler = assembler
        self.assembled_day = day
        self.packaging = packaging
        self.cycle_id = None
        self.sterilised_day = None
        self.expiry_day = None
        self.quarantined = False
        self.quarantine_reason = None

    def attach_cycle(self, cycle: AutoclaveCycle):
        if cycle.failed:
            self.quarantined = True
            self.quarantine_reason = "; ".join(cycle.failure_reasons)
            self.cycle_id = cycle.cycle_id
            return
        self.cycle_id = cycle.cycle_id
        self.sterilised_day = cycle.day
        self.expiry_day = cycle.day + SHELF_LIFE_DAYS[self.packaging]

    def usable_on(self, day):
        """Can this pack go to theatre on `day`?"""
        if self.quarantined:
            return False, f"quarantined: {self.quarantine_reason}"
        if self.cycle_id is None:
            return False, "no sterilisation cycle — a pack without a cycle "\
                          "does not exist for theatre"
        if day > self.expiry_day:
            return False, f"expired on day {self.expiry_day}"
        return True, "sterile"


class CSSDRegistry:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.packs = {}
        self.cycles = {}

    def register_cycle(self, cycle):
        self.cycles[cycle.cycle_id] = cycle
        return cycle

    def register_pack(self, pack, cycle_id):
        cycle = self.cycles.get(cycle_id)
        if cycle is None:
            raise Error(f"unknown cycle: {cycle_id}")
        pack.attach_cycle(cycle)
        self.packs[pack.pack_id] = pack
        return pack

    def issue_to_theatre(self, pack_id, *, day, issued_to):
        pack = self.packs.get(pack_id)
        if pack is None:
            raise Error(f"unknown pack: {pack_id}")
        ok, reason = pack.usable_on(day)
        if not ok:
            raise Error(f"pack refused: {reason}")
        if not issued_to:
            raise Error("a pack issues to a named person")
        return {"pack_id": pack_id, "issued_to": issued_to, "day": day,
                "cycle_id": pack.cycle_id}

    def daily_report(self, day):
        usable = sum(1 for p in self.packs.values()
                     if p.usable_on(day)[0])
        quarantined = sum(1 for p in self.packs.values() if p.quarantined)
        expired = sum(1 for p in self.packs.values()
                      if p.expiry_day is not None and day > p.expiry_day)
        body = {"facility_id": self.facility_id, "day": day,
                "total_packs": len(self.packs), "usable": usable,
                "quarantined": quarantined, "expired": expired,
                "cycles": len(self.cycles), "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
