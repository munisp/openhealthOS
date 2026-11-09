
"""kinnotify.py — consented next-of-kin notification engine.

A patient crashes at 02:00. Who gets told, how fast, and how do we
know they were reached? Today: a nurse with a personal phone and no
record. This module makes kin notification a governed lane: contacts
are consented registry entries, the message class decides the channel
plan, and every attempt is on the record with its outcome.

Doctrine:
- NO CONTACT WITHOUT CONSENT: a kin entry starts 'proposed'; only the
  patient's recorded consent (or a declared next-of-kin legal basis
  when the patient cannot consent) activates it.
- MESSAGE CLASSES, NOT FREE TEXT: 'critical', 'admission', 'discharge'
  — each with a declared channel plan. Clinical detail NEVER travels
  on a notification; the message is an event and a callback number.
- DELIVERY IS PROVEN OR STATED UNKNOWN: attempted / delivered /
  failed per channel; unknown is stated, never assumed delivered.
"""

VERSION = "kinnotify@1.0.0"

MESSAGE_CLASSES = ("critical", "admission", "discharge", "routine")
DEFAULT_PLANS = {
    "critical":  ["voice", "sms", "whatsapp"],
    "admission": ["sms", "whatsapp"],
    "discharge": ["sms"],
    "routine":   ["sms"],
}


class KinError(Exception):
    pass


class KinRegistry:
    def __init__(self):
        self._contacts = {}  # (patient_ref, contact_id) -> entry

    def propose(self, patient_ref, contact_id, *, name, phone, relation):
        key = (patient_ref, contact_id)
        if key in self._contacts:
            raise KinError("duplicate contact")
        self._contacts[key] = {"name": name, "phone": phone,
                               "relation": relation, "state": "proposed"}

    def activate(self, patient_ref, contact_id, *, basis):
        if basis not in ("patient-consent", "legal-next-of-kin"):
            raise KinError(f"unknown activation basis: {basis}")
        e = self._get(patient_ref, contact_id)
        e["state"] = "active"
        e["basis"] = basis

    def _get(self, patient_ref, contact_id):
        e = self._contacts.get((patient_ref, contact_id))
        if e is None:
            raise KinError("unknown contact")
        return e

    def active_contacts(self, patient_ref):
        return [dict(e, contact_id=cid)
                for (pref, cid), e in sorted(self._contacts.items())
                if pref == patient_ref and e["state"] == "active"]


class Notifier:
    """sender: callable(channel, phone, message) -> 'delivered'|'failed'.
    Injected — tests use a scripted sender, production binds the real
    voice/SMS/WhatsApp lanes."""

    def __init__(self, registry, sender, plans=None):
        self.registry = registry
        self.sender = sender
        self.plans = dict(plans or DEFAULT_PLANS)
        self.log = []

    def notify(self, patient_ref, *, message_class, event_ref, callback):
        if message_class not in MESSAGE_CLASSES:
            raise KinError(f"unknown message class: {message_class}")
        contacts = self.registry.active_contacts(patient_ref)
        if not contacts:
            return {"notified": 0, "note": "no active contacts — stated, "
                                           "not silently skipped"}
        # the message is an event + callback, never clinical detail
        message = f"[OpenHealth] event {event_ref} ({message_class}). "                   f"Please call {callback}."
        outcomes = []
        for c in contacts:
            delivered = False
            for channel in self.plans[message_class]:
                try:
                    result = self.sender(channel, c["phone"], message)
                except Exception:
                    result = "failed"   # channel exceptions are failures
                self.log.append({"patient_ref": patient_ref,
                                 "contact_id": c["contact_id"],
                                 "channel": channel, "outcome": result,
                                 "event_ref": event_ref})
                if result == "delivered":
                    delivered = True
                    break   # one delivered channel is enough
            outcomes.append({"contact_id": c["contact_id"],
                             "reached": delivered})
        return {"notified": sum(1 for o in outcomes if o["reached"]),
                "outcomes": outcomes}
