"""ebs.py — event-based surveillance: the rumour inbox.

Count-based surveillance (Scout) catches what facilities report. Outbreaks
also move on signals that never touch a facility form: a radio call-in, a
community leader's phone call, a pharmacy's unusual anti-diarrhoeal
sales, a religious gathering with unexplained deaths. EBS gives those
signals a disciplined inbox:

  intake -> dedupe (simhash — the same rumour arrives via five channels)
         -> triage (assign a verifier)
         -> verify (confirmed | dismissed | unresolved)
         -> confirmed signals become Scout-compatible drafts

Doctrine:
  - A RUMOUR IS NEVER AN ALERT. Only a verified signal leaves this
    inbox. Unverified items may be triaged, assigned, escalated for
    verification — but the word "alert" is reserved for verified;
  - dedupe REPORTS the channels, it does not erase them: five
    independent channels reporting the same event is corroboration
    evidence, recorded as such;
  - every state change carries who and when — in a panic, the audit
    trail is what separates discipline from noise.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "docclean"))
from docclean import DedupIndex, Error as DedupError  # noqa: E402

SOURCES = ("community-call", "radio", "pharmacy", "facility-phone",
           "chw-report", "poe", "social-media")
STATES = ("inbox", "triaged", "verifying", "verified", "dismissed")


class EBSError(Exception):
    pass


class SignalInbox:
    def __init__(self, dedup_threshold: int = 3):
        self.items = {}
        self.dedup = DedupIndex(threshold=dedup_threshold)
        self._seq = 0

    def intake(self, text: str, source: str, area: str, day: str,
               reporter: str) -> dict:
        if source not in SOURCES:
            raise EBSError(f"unknown source: {source}")
        if not text.strip() or not area.strip():
            raise EBSError("signal text and area are required")
        try:
            dup = self.dedup.check(f"sig-{self._seq + 1}", text)
        except DedupError:
            raise EBSError("empty signal after tokenisation")
        self._seq += 1
        sid = f"EBS-{self._seq:05d}"
        corroborates = [m[0] for m in dup["matches"]]
        item = {"id": sid, "text": text, "area": area, "day": day,
                "state": "inbox", "verifier": None,
                "channels": [{"source": source, "reporter": reporter,
                              "day": day}],
                "corroborates": corroborates,
                "history": [("inbox", day, reporter)]}
        self.items[sid] = item
        return {"id": sid, "duplicate_of": corroborates or None,
                "state": "inbox"}

    def corroborate(self, existing_id: str, source: str, reporter: str,
                    day: str):
        """Another channel reports the SAME event: corroboration, not a
        new signal."""
        item = self._item(existing_id)
        item["channels"].append({"source": source, "reporter": reporter,
                                 "day": day})

    def triage(self, sid: str, verifier: str, by: str, day: str):
        item = self._item(sid)
        if item["state"] != "inbox":
            raise EBSError(f"cannot triage from {item['state']}")
        if not verifier.strip():
            raise EBSError("a verifier is required — unowned signals rot")
        item["verifier"] = verifier
        self._set(item, "triaged", day, by)

    def begin_verification(self, sid: str, day: str):
        item = self._item(sid)
        if item["state"] != "triaged":
            raise EBSError("assign a verifier first")
        self._set(item, "verifying", day, item["verifier"])

    def resolve(self, sid: str, outcome: str, note: str, day: str) -> dict:
        """outcome: verified | dismissed. Verified signals return a
        Scout-compatible draft — the ONLY exit that becomes an alert
        upstream."""
        item = self._item(sid)
        if item["state"] not in ("triaged", "verifying"):
            raise EBSError(f"cannot resolve from {item['state']}")
        if outcome not in ("verified", "dismissed"):
            raise EBSError(f"outcome must be verified|dismissed")
        if not note.strip():
            raise EBSError("resolution note required — the reason IS the "
                           "record")
        self._set(item, outcome, day, item["verifier"])
        item["resolution_note"] = note
        if outcome == "verified":
            return {"signal_draft": {
                "kind": "ebs-verified-signal",
                "area": item["area"],
                "channels": len(item["channels"]),
                "text": item["text"],
                "note": note,
                "requires": "human epidemiologist review"}}
        return {"signal_draft": None}

    def queue(self) -> dict:
        return {
            "inbox": sorted(i["id"] for i in self.items.values()
                            if i["state"] == "inbox"),
            "awaiting_verification": sorted(
                i["id"] for i in self.items.values()
                if i["state"] in ("triaged", "verifying")),
            "verified": sorted(i["id"] for i in self.items.values()
                               if i["state"] == "verified"),
            "dismissed": sorted(i["id"] for i in self.items.values()
                                if i["state"] == "dismissed"),
        }

    def _item(self, sid):
        if sid not in self.items:
            raise EBSError(f"no such signal: {sid}")
        return self.items[sid]

    def _set(self, item, state, day, who):
        item["state"] = state
        item["history"].append((state, day, who))
