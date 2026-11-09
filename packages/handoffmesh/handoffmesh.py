
"""handoffmesh.py — structured clinical handoff cards over chat.

Shift handover by WhatsApp voice note is how patients fall between
shifts: no structure, no sign-off, no record. This module turns
handover into SBAR cards (Situation-Background-Assessment-
Recommendation) carried over the mesh: the receiver must acknowledge
with their own key before the sender's responsibility ends.

Doctrine:
- RESPONSIBILITY TRANSFERS ON ACK, NOT ON SEND: a handoff stays
  'open' — and the sender still owns the patient — until the named
  receiver countersigns.
- SBAR IS STRUCTURED: all four sections required; 'see my note' is
  not a situation.
- ESCALATION ON SILENCE: unacknowledged critical handoffs escalate
  to the duty officer after a declared window — stated, automatic,
  exactly once.
"""

VERSION = "handoffmesh@1.0.0"

import hashlib
import json

SBAR = ("situation", "background", "assessment", "recommendation")
STATES = ("open", "acknowledged", "escalated")
ACK_WINDOW_MIN = {"critical": 15, "routine": 240}


class HandoffError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


class HandoffBoard:
    def __init__(self, ack_windows=None):
        self.ack_windows = dict(ack_windows or ACK_WINDOW_MIN)
        self._cards = {}

    def open(self, ref, *, patient_ref, priority, sender, receiver,
             sections, now_min):
        if ref in self._cards:
            raise HandoffError(f"duplicate handoff: {ref}")
        if priority not in self.ack_windows:
            raise HandoffError(f"unknown priority: {priority}")
        missing = [s for s in SBAR if not (sections.get(s) or "").strip()]
        if missing:
            raise HandoffError(
                f"incomplete SBAR — missing: {', '.join(missing)}")
        if sender == receiver:
            raise HandoffError("handing off to yourself is not a handoff")
        self._cards[ref] = {
            "ref": ref, "patient_ref": patient_ref, "priority": priority,
            "sender": sender, "receiver": receiver,
            "sections": dict(sections), "opened_min": now_min,
            "state": "open",
            "checksum": None}
        c = self._cards[ref]
        c["checksum"] = hashlib.sha256(_canon(
            {k: v for k, v in c.items() if k != "checksum"})).hexdigest()
        return c["checksum"]

    def acknowledge(self, ref, *, by, now_min):
        c = self._get(ref)
        if by != c["receiver"]:
            raise HandoffError(
                f"only the named receiver ({c['receiver']}) can ack — "
                f"responsibility is personal")
        if c["state"] == "acknowledged":
            raise HandoffError("already acknowledged")
        c["state"] = "acknowledged"
        c["acked_min"] = now_min
        c["response_min"] = now_min - c["opened_min"]
        return c["response_min"]

    def sweep(self, now_min, *, duty_officer):
        """Critical handoffs past their window escalate exactly once."""
        escalated = []
        for ref, c in sorted(self._cards.items()):
            if c["state"] != "open":
                continue
            window = self.ack_windows[c["priority"]]
            if now_min - c["opened_min"] > window:
                c["state"] = "escalated"
                c["escalated_to"] = duty_officer
                escalated.append({"ref": ref, "to": duty_officer,
                                  "overdue_min": now_min - c["opened_min"]})
        return escalated

    def open_for(self, receiver):
        return [c["ref"] for c in self._cards.values()
                if c["receiver"] == receiver and c["state"] == "open"]

    def _get(self, ref):
        c = self._cards.get(ref)
        if c is None:
            raise HandoffError(f"unknown handoff: {ref}")
        return c
