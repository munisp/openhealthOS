"""crvs.py — civil registration bridge: making births and deaths count.

A child who is never registered is invisible to every plan, budget,
and vaccine list. Nigeria registers barely half its births; deaths
fare worse. This module is the bridge between facility/community
records and the civil registry — and its first job is measuring the
invisibility itself.

Doctrine:
- FAIL-CLOSED MINIMUM FIELDS: a registration without the legal minimum
  (event day, place, sex for births; event day, place, age for deaths)
  is refused — a half-record in the national registry is worse than
  a queued one, because it poisons downstream statistics silently.
- OFFLINE QUEUE IS FIRST-CLASS: records queue locally and sync when
  the link returns. Queue depth and queue age are reported, not hidden.
- INVISIBILITY IS A STATISTIC: estimated unregistered events (births
  seen by the platform but not yet registered) are surfaced per ward.
"""

BIRTH_MIN_FIELDS = ("event_day", "place", "sex", "mother_id")
DEATH_MIN_FIELDS = ("event_day", "place", "age_days", "sex")
QUEUE_STALE_DAYS = 14


class CRVSError(Exception):
    pass


class RegistrationBridge:
    def __init__(self):
        self.queue = []        # pending records (offline-first)
        self.registered = []   # confirmed by the registry
        self.seen_events = {"birth": set(), "death": set()}  # platform-known

    # ---- platform side: what the system knows happened ----
    def note_event(self, kind, event_id):
        if kind not in self.seen_events:
            raise CRVSError(f"unknown event kind: {kind}")
        self.seen_events[kind].add(event_id)

    # ---- registration ----
    def submit(self, kind, record, *, day):
        """Validate minimum fields; queue for the registry. Never drops."""
        min_fields = (BIRTH_MIN_FIELDS if kind == "birth" else DEATH_MIN_FIELDS)
        missing = [f for f in min_fields if record.get(f) in (None, "")]
        if missing:
            raise CRVSError(f"missing minimum fields: {', '.join(missing)}")
        if record["event_day"] > day:
            raise CRVSError("event in the future?")
        rec = dict(record, kind=kind, queued_day=day)
        self.queue.append(rec)
        return rec

    def sync(self, registry_ack, *, day):
        """Push queue to registry. registry_ack(rec) -> reg_no or raises.
        A failed record STAYS queued — silence is not success."""
        remaining = []
        synced = 0
        for rec in self.queue:
            try:
                reg_no = registry_ack(rec)
            except Exception:
                remaining.append(rec)
                continue
            if not reg_no:
                remaining.append(rec)
                continue
            self.registered.append(dict(rec, reg_no=reg_no, registered_day=day))
            synced += 1
        self.queue = remaining
        return {"synced": synced, "still_queued": len(self.queue)}

    def invisibility_report(self, *, as_of):
        """How many platform-known events remain unregistered, per kind,
        plus queue age so a dead link can't hide behind the count."""
        out = {"as_of": as_of, "by_kind": {}}
        reg_ids = {(r["kind"], r.get("event_id")) for r in self.registered}
        queued_ids = {(r["kind"], r.get("event_id")) for r in self.queue}
        for kind, events in self.seen_events.items():
            invisible = [e for e in events
                         if (kind, e) not in reg_ids and (kind, e) not in queued_ids]
            out["by_kind"][kind] = {
                "known_events": len(events),
                "registered": sum(1 for k, _ in reg_ids if k == kind),
                "queued": sum(1 for k, _ in queued_ids if k == kind),
                "unregistered_unqueued": len(invisible),
            }
        stale = [r for r in self.queue if as_of - r["queued_day"] > QUEUE_STALE_DAYS]
        out["queue_depth"] = len(self.queue)
        out["queue_stale"] = len(stale)
        out["note"] = ("unregistered_unqueued children are invisible to "
                       "every plan — find and register them")
        return out
