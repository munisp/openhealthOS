"""teleconsulta.py — store-and-forward teleconsultation network.

Cabo Verde's teleconsulta network exists because nine inhabited islands
cannot each hold every specialty. The peripheral facility packages a
case — history, vitals, images as attachment refs — into a checksummed
bundle; the hub specialist answers when connectivity and clinic
permit. The network's promise is a clock, not a hope.

Doctrine:
- THE BUNDLE IS THE UNIT: a consult travels as a checksum-stamped
  bundle. It can ride a sync window, a USB stick, or a boat — the
  checksum is the contract at the other end.
- STORE-AND-FORWARD IS FIRST-CLASS: no consult requires live
  connectivity. Deferred transmission is a state, not an error.
- THE SLA IS STATED PER PRIORITY: routine, urgent and emergency carry
  declared response windows; a breach is computed, never argued.
- THE HUB ANSWERS WITH A PLAN: a response without a management plan
  is not a response. Escalation to a live channel (tele-ICU, medevac)
  names the receiving channel.
- SPECIALTIES AND FACILITIES ARE ENTRIES: dermatology on Santo Antão
  and cardiology in Praia are registry rows.
"""

import hashlib
import json

VERSION = "teleconsulta@1.0.0"

PRIORITIES = ("routine", "urgent", "emergency")

# Declared response windows in minutes, per priority. Policy, stated.
SLA_MINUTES = {"routine": 4320, "urgent": 480, "emergency": 60}

STATES = ("draft", "bundled", "transmitted", "answered", "escalated",
          "closed")

# Starter specialty registry — loaded as entries in production.
SPECIALTIES = ("dermatology", "cardiology", "radiology", "paediatrics",
               "psychiatry", "ophthalmology")

ESCALATION_CHANNELS = ("teleicu", "medevac", "telederm-live")


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class TeleconsultaNetwork:
    """The network: facilities, consults, clocks."""

    def __init__(self):
        self.facilities = {}   # facility_id -> {name, kind, specialties}
        self.consults = {}

    # --- registry ------------------------------------------------------
    def register_facility(self, facility_id, name, *, kind,
                          specialties=()):
        if kind not in ("peripheral", "hub"):
            raise Error("a facility is peripheral or hub")
        if facility_id in self.facilities:
            raise Error(f"facility {facility_id} already registered")
        unknown = set(specialties) - set(SPECIALTIES)
        if unknown:
            raise Error(f"unknown specialties {sorted(unknown)}; "
                        "specialties are entries")
        if kind == "hub" and not specialties:
            raise Error("a hub declares the specialties it covers")
        self.facilities[facility_id] = {
            "name": name, "kind": kind,
            "specialties": tuple(specialties)}

    # --- lifecycle -------------------------------------------------------
    def create(self, consult_id, patient_ref, origin, specialty, *,
               priority, by, day, minute, history, attachments=()):
        if consult_id in self.consults:
            raise Error(f"consult {consult_id} already exists")
        facility = self._facility(origin)
        if facility["kind"] != "peripheral":
            raise Error("consults originate at a peripheral facility — "
                        "a hub asking itself is a ward round")
        if specialty not in SPECIALTIES:
            raise Error(f"unknown specialty {specialty!r}")
        if priority not in PRIORITIES:
            raise Error(f"unknown priority {priority!r}")
        if not history:
            raise Error("a consult carries clinical history — a bare "
                        "image is not a referral")
        if not by:
            raise Error("a consult names its requesting clinician")
        self.consults[consult_id] = {
            "consult_id": consult_id, "patient_ref": patient_ref,
            "origin": origin, "specialty": specialty,
            "priority": priority, "history": history,
            "attachments": list(attachments),
            "created": {"day": day, "minute": minute, "by": by},
            "sla_minutes": SLA_MINUTES[priority],
            "state": "draft", "response": None,
            "events": [{"event": "created", "day": day, "minute": minute,
                        "by": by}],
        }
        return self.consults[consult_id]

    def bundle(self, consult_id, *, by, day, minute):
        """Seal the consult into a transmittable bundle. The checksum
        is computed over the full content — what leaves is what
        arrives, or the receiver refuses."""
        c = self._in_state(consult_id, "draft")
        if by != c["created"]["by"]:
            raise Error("the requesting clinician seals their own "
                        "bundle")
        bundle = {k: c[k] for k in
                  ("consult_id", "patient_ref", "origin", "specialty",
                   "priority", "history", "attachments", "created")}
        bundle["version"] = VERSION
        c["bundle_sha256"] = _sha(bundle)
        c["state"] = "bundled"
        c["events"].append({"event": "bundled", "day": day,
                            "minute": minute, "by": by})
        return {"bundle": bundle, "sha256": c["bundle_sha256"]}

    def transmit(self, consult_id, *, day, minute, medium="sync"):
        """Mark the bundle as sent. Medium is stated — sync, usb, or
        boat are all honest ways across nine islands."""
        c = self._in_state(consult_id, "bundled")
        c["state"] = "transmitted"
        c["events"].append({"event": "transmitted", "day": day,
                            "minute": minute, "medium": medium})

    def receive(self, consult_id, hub, *, bundle_sha256, day, minute):
        """The hub verifies the checksum before reading — a torn bundle
        is refused, never partially interpreted."""
        c = self._in_state(consult_id, "transmitted")
        facility = self._facility(hub)
        if facility["kind"] != "hub":
            raise Error("only a hub receives consults")
        if c["specialty"] not in facility["specialties"]:
            raise Error(f"{hub} does not cover {c['specialty']} — "
                        "route to a hub that does")
        if bundle_sha256 != c["bundle_sha256"]:
            raise Error("bundle checksum mismatch — the bundle that "
                        "arrived is not the bundle that left")
        c["hub"] = hub
        c["events"].append({"event": "received", "day": day,
                            "minute": minute, "hub": hub})

    def answer(self, consult_id, *, specialist, day, minute, assessment,
               plan):
        c = self._in_state(consult_id, "transmitted")
        if not c.get("hub"):
            raise Error("the hub receives before it answers")
        if not specialist:
            raise Error("a response names the responding specialist")
        if not (assessment and plan):
            raise Error("a response without a management plan is not "
                        "a response")
        elapsed = (day - c["created"]["day"]) * 1440 + (
            minute - c["created"]["minute"])
        c["response"] = {
            "specialist": specialist, "day": day, "minute": minute,
            "assessment": assessment, "plan": plan,
            "response_minutes": elapsed,
            "sla_minutes": c["sla_minutes"],
            "sla_met": elapsed <= c["sla_minutes"],
        }
        c["state"] = "answered"
        c["events"].append({"event": "answered", "day": day,
                            "minute": minute, "by": specialist})
        return c["response"]

    def escalate(self, consult_id, *, by, day, minute, channel, reason):
        c = self._get(consult_id)
        if c["state"] in ("closed",):
            raise Error(f"consult is {c['state']}")
        if channel not in ESCALATION_CHANNELS:
            raise Error(f"unknown escalation channel {channel!r}; "
                        f"channels are {ESCALATION_CHANNELS}")
        if not reason:
            raise Error("escalation carries a stated reason")
        c["state"] = "escalated"
        c["escalation"] = {"channel": channel, "reason": reason,
                           "day": day, "minute": minute, "by": by}
        c["events"].append({"event": "escalated", "day": day,
                            "minute": minute, "by": by,
                            "channel": channel})

    def close(self, consult_id, *, by, day, minute, outcome):
        c = self._get(consult_id)
        if c["state"] not in ("answered", "escalated"):
            raise Error("only an answered or escalated consult closes")
        if not outcome:
            raise Error("closure states the outcome")
        c["state"] = "closed"
        c["outcome"] = outcome
        c["events"].append({"event": "closed", "day": day,
                            "minute": minute, "by": by})

    # --- measurement -----------------------------------------------------
    def sla_report(self):
        answered = [c for c in self.consults.values()
                    if c["response"] is not None]
        open_consults = [c for c in self.consults.values()
                         if c["state"] in ("bundled", "transmitted")]
        payload = {
            "consults": len(self.consults),
            "answered": len(answered),
            "sla_met": sum(1 for c in answered
                           if c["response"]["sla_met"]),
            "sla_breached": sum(1 for c in answered
                                if not c["response"]["sla_met"]),
            "open": len(open_consults),
            "escalated": sum(1 for c in self.consults.values()
                             if c["state"] == "escalated"),
            "version": VERSION,
        }
        payload["sha256"] = _sha(payload)
        return payload

    def _facility(self, facility_id):
        f = self.facilities.get(facility_id)
        if f is None:
            raise Error(f"unknown facility {facility_id!r}")
        return f

    def _get(self, consult_id):
        c = self.consults.get(consult_id)
        if c is None:
            raise Error(f"unknown consult {consult_id}")
        return c

    def _in_state(self, consult_id, state):
        c = self._get(consult_id)
        if c["state"] != state:
            raise Error(f"consult {consult_id} is {c['state']}, "
                        f"not {state}")
        return c
