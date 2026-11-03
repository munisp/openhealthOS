"""persist.py — durability for the device layer.

In-memory stores are correct until the process restarts — and the
spool is precisely the layer that exists to lose nothing. Doctrine:

  - the journal is APPEND-ONLY JSONL, fsync'd per write: a record the
    OS hasn't acknowledged is a record you don't have;
  - restore is replay: every durable store rebuilds from the event
    stream, so the journal IS the state — no shadow formats to drift;
  - a journal that cannot be read refuses the boot: starting fresh
    over a corrupted journal is silent data loss wearing a clean log.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from devicelink import FeedHealth, ResultStore, Spool  # noqa: E402
from devicehub import DeviceHubError  # noqa: E402
from patientlink import IdentityMap  # noqa: E402

PERSIST_VERSION = "device-persist@1.0.0"


class Journal:
    """Append-only JSONL with fsync discipline."""

    def __init__(self, path):
        if not path:
            raise DeviceHubError("a journal needs a path — durability "
                                 "to nowhere is not durability")
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)),
                    exist_ok=True)
        # refuse to boot over a corrupted journal
        for i, _ in enumerate(self.events()):
            pass

    def append(self, event):
        line = json.dumps(event, default=str)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def events(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise DeviceHubError(
                        f"journal {self.path} line {n} is corrupt "
                        f"({exc}) — refusing to boot over it; repair "
                        "or retire the file, never silently start "
                        "fresh") from exc


class DurableSpool(Spool):
    def __init__(self, journal):
        self.journal = journal
        super().__init__()
        for ev in journal.events():
            if ev["op"] == "enqueue":
                self.entries.append(ev["entry"])
                self._seq = max(self._seq,
                                int(ev["entry"]["spool_id"][3:]))
            elif ev["op"] == "state":
                for e in self.entries:
                    if e["spool_id"] == ev["spool_id"]:
                        e.update({k: v for k, v in ev.items()
                                  if k not in ("op", "spool_id")})

    def enqueue(self, session_id, direction, payload):
        entry = super().enqueue(session_id, direction, payload)
        self.journal.append({"op": "enqueue", "entry": dict(entry)})
        return entry

    def replay(self, session_id, deliver):
        out = super().replay(session_id, deliver)
        for e in self.entries:
            if e["session_id"] == session_id and (
                    e["attempts"] or e["state"] != "queued"):
                self.journal.append({"op": "state",
                                     "spool_id": e["spool_id"],
                                     "state": e["state"],
                                     "attempts": e["attempts"],
                                     **({"dead_reason":
                                         e["dead_reason"]}
                                        if "dead_reason" in e else {})})
        return out


class DurableResultStore(ResultStore):
    def __init__(self, journal):
        self.journal = journal
        super().__init__()
        for ev in journal.events():
            if ev["op"] == "post":
                super().post(ev["parsed"])

    def post(self, parsed):
        out = super().post(parsed)
        if out["verdict"] != "duplicate":
            self.journal.append({"op": "post", "parsed": parsed})
        return out


class DurableIdentityMap(IdentityMap):
    def __init__(self, journal):
        self.journal = journal
        super().__init__()
        for ev in journal.events():
            if ev["op"] == "register":
                super().register(ev["mrn"], ev["platform_id"],
                                 by=ev["by"], source=ev["source"])
            elif ev["op"] == "merge":
                super().merge(ev["surviving"], ev["retired"],
                              by=ev["by"])

    def register(self, mrn, platform_id, *, by, source="adt"):
        super().register(mrn, platform_id, by=by, source=source)
        self.journal.append({"op": "register", "mrn": mrn,
                             "platform_id": platform_id, "by": by,
                             "source": source})

    def merge(self, surviving_mrn, retired_mrn, *, by):
        super().merge(surviving_mrn, retired_mrn, by=by)
        self.journal.append({"op": "merge", "surviving": surviving_mrn,
                             "retired": retired_mrn, "by": by})


class DurableFeedHealth(FeedHealth):
    def __init__(self, journal):
        self.journal = journal
        super().__init__()
        for ev in journal.events():
            if ev["op"] == "expectation":
                self.expectations[ev["session_id"]] = ev["max_silence_s"]
            elif ev["op"] == "quarantine":
                self.quarantined[ev["session_id"]] = ev["detail"]
            elif ev["op"] == "clear":
                self.quarantined.pop(ev["session_id"], None)

    def set_expectation(self, session_id, max_silence_s):
        super().set_expectation(session_id, max_silence_s)
        self.journal.append({"op": "expectation",
                             "session_id": session_id,
                             "max_silence_s": max_silence_s})

    def note_parse_failure(self, session_id):
        was = session_id in self.quarantined
        super().note_parse_failure(session_id)
        if not was and session_id in self.quarantined:
            self.journal.append({"op": "quarantine",
                                 "session_id": session_id,
                                 "detail": dict(
                                     self.quarantined[session_id])})

    def clear_quarantine(self, session_id, *, by, reason):
        out = super().clear_quarantine(session_id, by=by, reason=reason)
        self.journal.append({"op": "clear", "session_id": session_id,
                             "by": by, "reason": reason})
        return out
