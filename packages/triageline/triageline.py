"""triageline.py — citizen triage line (the SNS 24 pattern).

A national advice line answers two questions honestly: "what is the
right disposition for this caller?" and "did we call back when we said
we would?". A line that cannot measure its own broken promises is a
press release, not a service.

Doctrine:
- THE PROTOCOL IS A REGISTRY ENTRY: symptom streams, discriminators,
  urgencies and dispositions are versioned entries. Changing the
  protocol is ratification, not a code edit.
- EVERY CALL REACHES A DISPOSITION: a call without an outcome is an
  open obligation, not a statistic.
- CALLBACK IS A PROMISE WITH A CLOCK: the SLA is stated at booking;
  breaches are computed, never estimated.
- HIGH-URGENCY IS A HANDOFF, NOT A NOTE: emergency dispositions must
  be handed to a named service; "told them to go" is not a handoff.
- QA SAMPLES ARE REVIEWED FOUR-EYES: a reviewer never audits their
  own call.
"""

import hashlib
import json

VERSION = "triageline@1.0.0"

URGENCIES = ("emergency", "urgent", "routine", "self-care")

DISPOSITIONS = ("dispatch-emergency", "same-day-clinic",
                "book-appointment", "callback", "self-care-advice")

# Minutes within which each disposition must be completed.
DISPOSITION_SLA_MIN = {"dispatch": 8, "callback": 120}

# A minimal starter protocol set — real streams are loaded as entries.
STARTER_PROTOCOLS = {
    "chest-pain": {
        "version": "chest-pain@1",
        "discriminators": {
            "crushing-with-breathlessness": ("emergency",
                                             "dispatch-emergency"),
            "exertional-only": ("urgent", "same-day-clinic"),
            "muscular-reproducible": ("routine", "self-care-advice"),
        },
    },
    "fever-adult": {
        "version": "fever-adult@1",
        "discriminators": {
            "stiff-neck-or-confusion": ("emergency",
                                        "dispatch-emergency"),
            "persistent-3-days": ("urgent", "same-day-clinic"),
            "mild-recent": ("routine", "self-care-advice"),
        },
    },
    "child-diarrhoea": {
        "version": "child-diarrhoea@1",
        "discriminators": {
            "lethargy-or-blood": ("emergency", "dispatch-emergency"),
            "poor-intake": ("urgent", "callback"),
            "mild": ("routine", "self-care-advice"),
        },
    },
}


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ProtocolRegistry:
    """Triage protocols are four-eyes entries: proposed, ratified by a
    second clinician, live. The line runs only live versions."""

    def __init__(self):
        self.protocols = dict(STARTER_PROTOCOLS)
        self.status = {k: "live" for k in STARTER_PROTOCOLS}
        self.ratifications = {}

    def propose(self, stream, definition, *, by, day):
        if stream in self.protocols:
            raise Error(f"stream {stream!r} exists; retire before "
                        "replacing")
        if not definition_ok(definition):
            raise Error("a protocol needs discriminators mapping to "
                        "urgency + disposition")
        self.protocols[stream] = definition
        self.status[stream] = "proposed"
        self.ratifications[stream] = {"proposed_by": by, "day": day}

    def ratify(self, stream, *, by, day):
        if self.status.get(stream) != "proposed":
            raise Error(f"nothing to ratify for {stream!r}")
        if by == self.ratifications[stream]["proposed_by"]:
            raise Error("the hand that proposes never approves")
        self.status[stream] = "live"
        self.ratifications[stream]["ratified_by"] = by
        self.ratifications[stream]["ratified_day"] = day

    def retire(self, stream, *, by, day, reason):
        if self.status.get(stream) != "live":
            raise Error(f"{stream!r} is not live")
        if not reason:
            raise Error("retirement carries a stated reason")
        self.status[stream] = "retired"

    def live(self, stream):
        if self.status.get(stream) != "live":
            raise Error(f"no live protocol for stream {stream!r}")
        return self.protocols[stream]


def definition_ok(definition):
    discriminators = definition.get("discriminators") if definition else None
    if not discriminators:
        return False
    for urgency, disposition in discriminators.values():
        if urgency not in URGENCIES or disposition not in DISPOSITIONS:
            return False
    return True


class TriageLine:
    """The line itself: calls, dispositions, promises, and their
    clocks."""

    def __init__(self, registry=None):
        self.registry = registry or ProtocolRegistry()
        self.calls = {}
        self.callbacks = []

    def open_call(self, call_id, caller_ref, *, handler, minute, day,
                  stream, language="en"):
        if call_id in self.calls:
            raise Error(f"call {call_id} already open")
        if not handler:
            raise Error("a call names its handler")
        protocol = self.registry.live(stream)  # refuses when not live
        self.calls[call_id] = {
            "call_id": call_id, "caller_ref": caller_ref,
            "handler": handler, "minute": minute, "day": day,
            "stream": stream, "language": language,
            "protocol_version": protocol["version"],
            "state": "open", "disposition": None, "events": [
                {"event": "opened", "day": day, "minute": minute,
                 "by": handler}],
        }
        return self.calls[call_id]

    def assess(self, call_id, discriminator, *, by, day, minute):
        call = self._open(call_id)
        if by != call["handler"]:
            raise Error("the handler assesses their own call")
        protocol = self.registry.live(call["stream"])
        outcome = protocol["discriminators"].get(discriminator)
        if outcome is None:
            raise Error(f"unknown discriminator {discriminator!r} for "
                        f"stream {call['stream']!r} — the protocol "
                        "refuses, it does not guess")
        urgency, disposition = outcome
        call["urgency"] = urgency
        call["disposition"] = disposition
        call["events"].append({
            "event": "assessed", "day": day, "minute": minute, "by": by,
            "discriminator": discriminator, "urgency": urgency,
            "disposition": disposition})
        return urgency, disposition

    def complete(self, call_id, *, by, day, minute, handed_to=None,
                 advice_given=""):
        call = self._open(call_id)
        if call["disposition"] is None:
            raise Error("no disposition yet — assess before completing")
        disp = call["disposition"]
        if disp == "dispatch-emergency":
            if not handed_to:
                raise Error("an emergency disposition is a handoff to "
                            "a named service, not a note")
            elapsed = (day - call["day"]) * 1440 + (minute - call["minute"])
            call["dispatch_sla_min"] = DISPOSITION_SLA_MIN["dispatch"]
            call["dispatch_met"] = elapsed <= DISPOSITION_SLA_MIN[
                "dispatch"]
        if disp == "callback":
            due_minute = minute + DISPOSITION_SLA_MIN["callback"]
            self.callbacks.append({
                "call_id": call_id, "due_day": day,
                "due_minute": due_minute,
                "booked_day": day, "booked_minute": minute,
                "state": "promised", "completed": None})
        if disp == "self-care-advice" and not advice_given:
            raise Error("self-care disposition states the advice given")
        call["state"] = "completed"
        call["completed"] = {"day": day, "minute": minute, "by": by,
                             "handed_to": handed_to,
                             "advice_given": advice_given}
        call["events"].append({"event": "completed", "day": day,
                               "minute": minute, "by": by})
        return call

    def complete_callback(self, call_id, *, by, day, minute, outcome):
        for cb in self.callbacks:
            if cb["call_id"] == call_id and cb["state"] == "promised":
                if not outcome:
                    raise Error("a completed callback states its outcome")
                late = (day, minute) > (cb["due_day"],
                                        cb["due_minute"])
                cb["state"] = "breached" if late else "kept"
                cb["completed"] = {"day": day, "minute": minute,
                                   "by": by, "outcome": outcome}
                return cb
        raise Error(f"no promised callback for {call_id}")

    # --- measurement ------------------------------------------------------
    def sla_report(self, day, minute):
        """The promise ledger: kept, breached, and still-open promises
        past due are all computed — never estimated."""
        kept = breached = overdue_open = open_ok = 0
        for cb in self.callbacks:
            if cb["state"] == "kept":
                kept += 1
            elif cb["state"] == "breached":
                breached += 1
            elif (day, minute) > (cb["due_day"], cb["due_minute"]):
                overdue_open += 1
            else:
                open_ok += 1
        return {"promises": len(self.callbacks), "kept": kept,
                "breached": breached, "overdue_open": overdue_open,
                "open_within_sla": open_ok,
                "dispatch_breaches": sum(
                    1 for c in self.calls.values()
                    if c.get("dispatch_met") is False),
                "version": VERSION,
                "sha256": _sha({"callbacks": self.callbacks,
                                "version": VERSION})}

    def qa_review(self, call_id, *, reviewer, day, verdict, note=""):
        call = self._get(call_id)
        if call["state"] != "completed":
            raise Error("QA reviews completed calls")
        if reviewer == call["handler"]:
            raise Error("a reviewer never audits their own call")
        if verdict not in ("sound", "coaching-needed", "unsafe"):
            raise Error("verdict is sound, coaching-needed, or unsafe")
        call["qa"] = {"reviewer": reviewer, "day": day,
                      "verdict": verdict, "note": note}
        call["events"].append({"event": "qa-reviewed", "day": day,
                               "by": reviewer, "verdict": verdict})

    def _open(self, call_id):
        call = self._get(call_id)
        if call["state"] != "open":
            raise Error(f"call {call_id} is {call['state']}")
        return call

    def _get(self, call_id):
        call = self.calls.get(call_id)
        if call is None:
            raise Error(f"unknown call {call_id}")
        return call
