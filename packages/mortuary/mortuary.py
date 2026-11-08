"""mortuary.py — deceased registration, storage, and release.

The mortuary is where the record meets the family. A body admitted
without a chain of custody, stored without a slot, or released without
identification is a scandal in a folder. The register is the defence.

Doctrine:
- DEATH IS CONFIRMED BY A NAMED CLINICIAN: no body enters the register
  without a death declaration naming who confirmed and when.
- STORAGE IS A SLOT: cold-room slots are finite and tracked; a body
  occupies exactly one slot at a time.
- RELEASE IS IDENTITY + AUTHORITY: the body leaves only to a named
  person with a stated relationship and a stated document (next-of-kin
  letter, police release, or burial permit) — never "someone came".
- THE REGISTER NEVER LOSES A BODY: transfers between slots are events;
  the chain of custody is append-only.
"""

import hashlib
import json

VERSION = "mortuary@1.0.0"

RELEASE_DOCUMENTS = ("next-of-kin-letter", "police-release",
                     "burial-permit", "hospital-admin-authorization")


class Error(Exception):
    pass


class ColdRoom:
    def __init__(self, room_id, capacity):
        if capacity <= 0:
            raise Error("a cold room has positive capacity")
        self.room_id = room_id
        self.capacity = capacity
        self.slots = {}  # slot_no -> body_id

    def assign(self, body_id):
        if body_id in self.slots.values():
            raise Error(f"body {body_id} already occupies a slot")
        free = [n for n in range(1, self.capacity + 1)
                if n not in self.slots]
        if not free:
            raise Error("cold room full — overflow is a stated event, "
                        "not a silent stack")
        slot = free[0]
        self.slots[slot] = body_id
        return slot

    def move(self, body_id, new_slot):
        current = None
        for n, b in self.slots.items():
            if b == body_id:
                current = n
                break
        if current is None:
            raise Error("body not in this room")
        if new_slot in self.slots:
            raise Error(f"slot {new_slot} occupied")
        del self.slots[current]
        self.slots[new_slot] = body_id
        return new_slot

    def release(self, body_id):
        for n, b in list(self.slots.items()):
            if b == body_id:
                del self.slots[n]
                return n
        raise Error("body not in this room")

    def occupancy(self):
        return {"room_id": self.room_id, "capacity": self.capacity,
                "occupied": len(self.slots),
                "free": self.capacity - len(self.slots)}


class MortuaryRegister:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.bodies = {}  # body_id -> record
        self._n = 0

    def declare_death(self, *, patient_ref, declared_by, day, minute,
                      cause_stated, place="facility"):
        if not declared_by:
            raise Error("a death declaration names the clinician")
        if not (cause_stated and cause_stated.strip()):
            raise Error("the cause of death is stated, not assumed")
        self._n += 1
        body_id = f"{self.facility_id}-BODY-{self._n}"
        self.bodies[body_id] = {
            "body_id": body_id, "patient_ref": patient_ref,
            "declared_by": declared_by, "day": day, "minute": minute,
            "cause_stated": cause_stated, "place": place,
            "custody": [{"event": "declared", "by": declared_by,
                         "day": day, "minute": minute}],
            "released": None,
        }
        return body_id

    def admit_to_cold_room(self, body_id, room: ColdRoom, *, by, day,
                           minute):
        body = self._body(body_id)
        slot = room.assign(body_id)
        body["custody"].append({"event": "stored", "room": room.room_id,
                                "slot": slot, "by": by, "day": day,
                                "minute": minute})
        return slot

    def transfer(self, body_id, room: ColdRoom, new_slot, *, by, day,
                 minute):
        body = self._body(body_id)
        room.move(body_id, new_slot)
        body["custody"].append({"event": "transferred",
                                "room": room.room_id, "slot": new_slot,
                                "by": by, "day": day, "minute": minute})

    def release(self, body_id, room: ColdRoom, *, released_to,
                relationship, document, by, day, minute):
        if document not in RELEASE_DOCUMENTS:
            raise Error(f"release requires one of {RELEASE_DOCUMENTS}")
        if not (released_to and released_to.strip()):
            raise Error("the body leaves to a named person")
        if not (relationship and relationship.strip()):
            raise Error("the relationship is stated")
        body = self._body(body_id)
        slot = room.release(body_id)
        body["released"] = {"to": released_to, "relationship": relationship,
                            "document": document, "by": by, "day": day,
                            "minute": minute, "slot_vacated": slot}
        body["custody"].append({"event": "released", "by": by, "day": day,
                                "minute": minute, "to": released_to})
        return body["released"]

    def _body(self, body_id):
        if body_id not in self.bodies:
            raise Error(f"unknown body: {body_id}")
        b = self.bodies[body_id]
        if b["released"]:
            raise Error(f"body {body_id} already released — the register "
                        "never loses a body")
        return b

    def register_snapshot(self):
        body = {"facility_id": self.facility_id,
                "in_custody": [b["body_id"] for b in self.bodies.values()
                               if not b["released"]],
                "released": len([b for b in self.bodies.values()
                                 if b["released"]]),
                "total_registered": len(self.bodies),
                "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
