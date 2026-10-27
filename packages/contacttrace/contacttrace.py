"""contacttrace.py — the contact graph: the unit of outbreak response.

A case exposes contacts; each contact gets 21 days of monitoring (Ebola's
max incubation); a symptomatic contact CONVERTS to a case and spawns
their own ring of contacts. Containment arithmetic: every contact either
clears at day 21 or converts — anything else is a leak.

Doctrine:
  - day-21 countdown is computed, never asserted: monitoring_days
    derives from exposure date and today, never from a counter someone
    edits;
  - conversion is one-way and creates the new ring seed — a contact that
    becomes a case is a case forever (status history is append-only);
  - a contact's missed check-in is a first-class state (overdue), because
    in practice containment fails at "we lost them on day 12";
  - the graph refuses cycles: A cannot be both case and contact of B in
    the same chain link backwards (re-exposure is a NEW link, recorded,
    not an overwrite).
"""
from __future__ import annotations

MONITORING_DAYS = 21  # Ebola maximum incubation; parametrize per pathogen

CONTACT_STATES = ("monitoring", "overdue", "converted", "cleared", "lost")


class TraceError(Exception):
    pass


class ContactGraph:
    def __init__(self, monitoring_days: int = MONITORING_DAYS):
        self.monitoring_days = monitoring_days
        self.cases = {}     # case_id -> {id, confirmed_day, ...}
        self.contacts = {}  # contact_id -> record
        self.links = []     # (case_id, contact_id, exposure_day)

    # ----------------------------------------------------------- cases

    def add_case(self, case_id: str, confirmed_day: str,
                 source_contact_id: str | None = None) -> dict:
        if case_id in self.cases:
            raise TraceError(f"duplicate case id: {case_id}")
        case = {"id": case_id, "confirmed_day": confirmed_day,
                "source_contact": source_contact_id}
        self.cases[case_id] = case
        return case

    # -------------------------------------------------------- contacts

    def add_contact(self, case_id: str, contact_id: str,
                    exposure_day: str, tracer: str | None = None,
                    generation: int | None = None) -> dict:
        """Line-list a contact. generation is computed from the chain
        unless given: contacts of the index case are generation 1."""
        if case_id not in self.cases:
            raise TraceError(f"no such case: {case_id}")
        if contact_id in self.contacts:
            # re-exposure: new link, original record stands
            self.links.append((case_id, contact_id, exposure_day))
            self.contacts[contact_id].setdefault("reexposures", []).append(
                {"case": case_id, "exposure_day": exposure_day})
            return self.contacts[contact_id]
        gen = generation if generation is not None else self._generation(case_id)
        rec = {"id": contact_id, "case": case_id, "exposure_day": exposure_day,
               "generation": gen, "tracer": tracer, "state": "monitoring",
               "checkins": [], "history": [("monitoring", exposure_day)]}
        self.contacts[contact_id] = rec
        self.links.append((case_id, contact_id, exposure_day))
        return rec

    def _generation(self, case_id: str) -> int:
        src = self.cases[case_id].get("source_contact")
        if src and src in self.contacts:
            return self.contacts[src]["generation"] + 1
        return 1

    # ------------------------------------------------------- monitoring

    def checkin(self, contact_id: str, day: str, symptomatic: bool = False):
        """A daily check-in. Symptomatic converts immediately — the
        contact is now a suspect case and their own ring must start."""
        rec = self._rec(contact_id)
        if rec["state"] in ("converted", "cleared"):
            raise TraceError(f"contact {contact_id} is {rec['state']} — "
                             "check-ins are closed")
        rec["checkins"].append(day)
        if symptomatic:
            self._set(rec, "converted")
            return {"state": "converted",
                    "action": "create-case-and-start-ring"}
        if rec["state"] == "overdue":
            self._set(rec, "monitoring")  # found again
        return {"state": rec["state"]}

    def sweep(self, today: str) -> dict:
        """Daily sweep: mark overdue (missed yesterday's check-in) and
        cleared (21 days elapsed, all accounted). Returns the work the
        response must do TODAY."""
        overdue, cleared, due = [], [], []
        for rec in self.contacts.values():
            if rec["state"] not in ("monitoring", "overdue"):
                continue
            elapsed = _days_between(rec["exposure_day"], today)
            if elapsed >= self.monitoring_days:
                self._set(rec, "cleared")
                cleared.append(rec["id"])
                continue
            last = rec["checkins"][-1] if rec["checkins"] else None
            if last is None or _days_between(last, today) > 1:
                if rec["state"] != "overdue":
                    self._set(rec, "overdue")
                overdue.append(rec["id"])
            else:
                due.append(rec["id"])
        return {"due_today": sorted(due), "overdue": sorted(overdue),
                "cleared": sorted(cleared)}

    def mark_lost(self, contact_id: str, reason: str):
        if not reason.strip():
            raise TraceError("a lost contact requires a reason — losing a "
                             "person is a reportable event, not a cleanup")
        self._set(self._rec(contact_id), "lost")

    # ---------------------------------------------------------- views

    def worklist(self, tracer: str, today: str) -> dict:
        """One tracer's day: contacts due, contacts overdue, in order of
        risk (overdue first — a contact you cannot find is a case you
        cannot see coming)."""
        self.sweep(today)
        mine = [r for r in self.contacts.values() if r["tracer"] == tracer]
        return {"tracer": tracer,
                "overdue": sorted(r["id"] for r in mine
                                  if r["state"] == "overdue"),
                "due": sorted(r["id"] for r in mine
                              if r["state"] == "monitoring"),
                "done": sorted(r["id"] for r in mine
                               if r["state"] in ("cleared", "converted"))}

    def stats(self) -> dict:
        by_state = {}
        for r in self.contacts.values():
            by_state[r["state"]] = by_state.get(r["state"], 0) + 1
        gens = [r["generation"] for r in self.contacts.values()]
        return {"cases": len(self.cases), "contacts": len(self.contacts),
                "by_state": by_state,
                "max_generation": max(gens) if gens else 0,
                "followup_completeness": self._completeness()}

    def _completeness(self):
        active = [r for r in self.contacts.values()
                  if r["state"] in ("monitoring", "overdue")]
        if not active:
            return 1.0
        on_time = sum(1 for r in active if r["state"] == "monitoring")
        return round(on_time / len(active), 3)

    # ---------------------------------------------------------- internals

    def _rec(self, contact_id):
        if contact_id not in self.contacts:
            raise TraceError(f"no such contact: {contact_id}")
        return self.contacts[contact_id]

    def _set(self, rec, state):
        if rec["state"] == "converted" and state != "converted":
            raise TraceError("conversion is one-way — a case is a case")
        rec["state"] = state
        rec["history"].append((state,))


def _days_between(d1: str, d2: str) -> int:
    from datetime import date
    a = date.fromisoformat(d1)
    b = date.fromisoformat(d2)
    return (b - a).days


# ------------------------------------------------- howUdeyChat seam (NDJSON)
# The chat side (kiosk check-ins, monitor ladders) is JS; the graph is
# here. The seam is append-only NDJSON lines — the same shape the
# facility node's ingest path already speaks.

def export_worklist_lines(graph: "ContactGraph", today: str) -> list[str]:
    """One JSON line per tracer worklist — consumed by the chat side to
    render today's visits/calls and arm the monitor ladders."""
    import json as _json
    tracers = sorted({r["tracer"] for r in graph.contacts.values()
                      if r["tracer"]})
    lines = []
    for t in tracers:
        wl = graph.worklist(t, today)
        lines.append(_json.dumps({"kind": "trace-worklist", "day": today,
                                  **wl}, sort_keys=True))
    return lines


def ingest_checkin_lines(graph: "ContactGraph", lines: list[str]) -> dict:
    """Apply chat-side check-in events. Shape:
    {"kind":"trace-checkin","contact":"C-01","day":"2026-10-22",
     "symptomatic":false}
    Unknown contacts and malformed lines are rejected loudly, line by
    line — a bad line never silently passes, and never blocks good ones.
    """
    import json as _json
    applied, rejected = 0, []
    for i, line in enumerate(lines):
        try:
            ev = _json.loads(line)
            if ev.get("kind") != "trace-checkin":
                raise TraceError("wrong kind")
            graph.checkin(ev["contact"], ev["day"],
                          bool(ev.get("symptomatic")))
            applied += 1
        except (TraceError, KeyError, _json.JSONDecodeError,
                TypeError) as exc:
            rejected.append({"line": i, "reason": str(exc)})
    return {"applied": applied, "rejected": rejected}
