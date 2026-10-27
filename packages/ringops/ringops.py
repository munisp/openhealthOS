"""ringops.py — ring vaccination / prophylaxis campaign manager.

When vaccine or PEP is deployed, the unit of work is the RING: all
contacts of a confirmed case, plus contacts-of-contacts where the
strategy requires. The ring is generated FROM the contact graph —
hand-assembled lists miss people; the graph already knows them.

Per-person states: eligible -> consented|declined -> dosed -> watched
(AEFI follow-up) -> complete. Ring completeness — dosed-or-declined over
eligible — is the metric that predicts containment, and it is computed,
never asserted.

Doctrine:
  - consent precedes dose, always — a dose without consent is a
    structural error, not a workflow shortcut;
  - declined is recorded with a reason and stays in the denominator —
    hiding refusals inflates completeness;
  - AEFI (adverse events) following immunisation ride the existing
    immig bundle shape: any post-dose event is recorded and the person
    flagged for follow-up;
  - contacts-of-contacts inclusion is a declared strategy parameter,
    never a silent choice.
"""
from __future__ import annotations

STATES = ("eligible", "consented", "declined", "dosed", "complete")


class RingError(Exception):
    pass


class Ring:
    def __init__(self, ring_id: str, case_id: str, members: list[str],
                 include_second_ring: bool = False,
                 second_ring: list[str] | None = None):
        if not members:
            raise RingError("a ring with zero members is not a ring")
        self.id = ring_id
        self.case = case_id
        self.include_second = include_second_ring
        self.people = {m: {"state": "eligible", "ring": 1}
                       for m in members}
        for m in (second_ring or []):
            if m not in self.people:
                self.people[m] = {"state": "eligible", "ring": 2}
        self.events = []

    @classmethod
    def from_graph(cls, ring_id: str, case_id: str, graph,
                   include_second_ring: bool = False) -> "Ring":
        """Generate from a ContactGraph: contacts of the case; optionally
        the contacts of those contacts' own cases (second ring)."""
        first = [cid for (c, cid, _) in graph.links if c == case_id]
        second = []
        if include_second_ring:
            for cid in first:
                for (c, ccid, _) in graph.links:
                    if c == cid or (c in graph.cases and
                                    graph.cases[c].get("source_contact") == cid):
                        second.append(ccid)
        return cls(ring_id, case_id, first, include_second_ring, second)

    def consent(self, person: str, day: str):
        p = self._p(person)
        if p["state"] != "eligible":
            raise RingError(f"cannot consent from {p['state']}")
        p["state"] = "consented"

    def decline(self, person: str, reason: str, day: str):
        if not reason.strip():
            raise RingError("declines require a reason — hesitancy is "
                            "data, not a gap")
        p = self._p(person)
        if p["state"] != "eligible":
            raise RingError(f"cannot decline from {p['state']}")
        p["state"] = "declined"
        p["decline_reason"] = reason

    def dose(self, person: str, day: str, vaccinator: str):
        p = self._p(person)
        if p["state"] != "consented":
            raise RingError(
                f"dose requires consent — {person} is {p['state']}")
        if not vaccinator.strip():
            raise RingError("vaccinator identity required")
        p["state"] = "dosed"
        p["dose_day"] = day
        p["vaccinator"] = vaccinator

    def record_aefi(self, person: str, description: str, day: str):
        p = self._p(person)
        if p["state"] not in ("dosed", "complete"):
            raise RingError("AEFI can only follow a dose")
        p["aefi"] = {"description": description, "day": day}
        p["followup_needed"] = True

    def complete(self, person: str, day: str):
        p = self._p(person)
        if p["state"] != "dosed":
            raise RingError("only dosed members complete follow-up")
        p["state"] = "complete"

    def completeness(self) -> dict:
        total = len(self.people)
        accounted = sum(1 for p in self.people.values()
                        if p["state"] in ("dosed", "complete", "declined"))
        declined = sum(1 for p in self.people.values()
                       if p["state"] == "declined")
        aefi = sum(1 for p in self.people.values() if "aefi" in p)
        return {"ring": self.id, "eligible": total,
                "dosed_or_complete": sum(1 for p in self.people.values()
                                         if p["state"] in ("dosed", "complete")),
                "declined": declined, "aefi_reports": aefi,
                "completeness": round(accounted / total, 3)}

    def _p(self, person):
        if person not in self.people:
            raise RingError(f"{person} is not in ring {self.id}")
        return self.people[person]
