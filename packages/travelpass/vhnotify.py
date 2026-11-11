"""vhnotify.py — proactive notifications: the scheme talks first. The
traveller hears about the purchase, the upcoming trip, the expiring
cover, and the claim moving — before they have to ask.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.8):
  - FR-HLP-02: proactive notifications — purchase confirmation,
    pre-travel reminder, expiry warning at day -3, extension nudges,
    claim status changes.

Doctrines:
- A notification rail without a transport is not a rail — the adapter
  refuses unplugged, and a failed send refuses loudly.
- Time-driven triggers fire from the schedule, once per (event, cover,
  day) — a reminder sent twice is spam with a timestamp.
- Language is the traveller's: content renders through the ContentLayer
  with the fallback stated, never silent.
- The outbox is append-only; denominators always visible.
"""

import secrets

VHNOTIFY_VERSION = "vhc-notify@1.0.0"

NOTIFY_RULES = {
    "vhc-notify@1.0.0": {
        "channels": ("email", "sms", "whatsapp", "push"),
        "events": ("purchase-confirmation", "pre-travel-reminder",
                   "expiry-warning", "extension-nudge",
                   "courtesy-issued", "courtesy-conversion",
                   "claim-status", "gop-issued", "refund-issued"),
        "pre_travel_days": 7,          # reminder a week out
    },
    # 1.1.0 adds the prevention-campaign trigger (vhseason). Rules are
    # versioned, never mutated — 1.0.0 stays byte-stable.
    "vhc-notify@1.1.0": {
        "channels": ("email", "sms", "whatsapp", "push"),
        "events": ("purchase-confirmation", "pre-travel-reminder",
                   "expiry-warning", "extension-nudge",
                   "courtesy-issued", "courtesy-conversion",
                   "claim-status", "gop-issued", "refund-issued",
                   "prevention-campaign"),
        "pre_travel_days": 7,
    },
}


class NotifyError(Exception):
    pass


def _refuse(msg):
    raise NotifyError(msg)


class NotificationOutbox:
    """The outbox. Event-driven sends are emitted by the modules (or
    the wiring above them); time-driven sends are produced by
    evaluate_schedule against the cover book. Both are idempotent per
    (event, ref, day) — replays return the original, never resend."""

    def __init__(self, transport, *, content=None,
                 rules_version="vhc-notify@1.0.0", id_fn=None):
        if transport is None:
            _refuse("a notification rail without a transport is not a "
                    "rail — plug in email/SMS/WhatsApp or say the "
                    "scheme is silent")
        if rules_version not in NOTIFY_RULES:
            _refuse("unknown notify rules version")
        self.transport = transport
        self.content = content       # optional vhsupport.ContentLayer
        self.rules = dict(NOTIFY_RULES[rules_version])
        # IDs stay secrets-unique in production; demos and
        # reproducibility harnesses inject a deterministic id_fn
        # (ADR-0079 — the checksum is the contract, so a demo must
        # be able to walk the REAL flow and still be byte-stable).
        self._id_fn = id_fn or (lambda: secrets.token_hex(4))
        self.outbox = []             # append-only
        self._sent = {}              # (event, ref, day) -> outbox idx

    def emit(self, event, *, ref, channel, lang, day, detail=None):
        """Send one notification. Idempotent per (event, ref, day):
        a replay returns the original record and sends nothing."""
        if event not in self.rules["events"]:
            _refuse("unknown event — the trigger list is versioned")
        if channel not in self.rules["channels"]:
            _refuse("unknown channel — the door list is config")
        if not ref:
            _refuse("a notification binds to a record")
        key = (event, ref, day)
        if key in self._sent:
            rec = self.outbox[self._sent[key]]
            return {"notification_id": rec["notification_id"],
                    "event": event, "ref": ref, "replayed": True}
        message = {"event": event, "ref": ref, "lang": lang,
                   "detail": detail or {}}
        if self.content is not None:
            rendered = self.content.get("notify." + event, lang=lang,
                                        version="v1")
            message["text"] = rendered["text"]
            message["text_lang"] = rendered["lang"]
            if rendered.get("fallback"):
                message["fallback_stated"] = rendered["note"]
        result = self.transport(channel, message)
        if not result or result.get("status") != "accepted":
            _refuse("notification send refused by the rail — a "
                    "swallowed failure is a traveller who never heard")
        rec = {"notification_id": "NTF-" + self._id_fn().upper(),
               "event": event, "ref": ref, "channel": channel,
               "lang": lang, "day": day}
        self._sent[key] = len(self.outbox)
        self.outbox.append(rec)
        return {"notification_id": rec["notification_id"],
                "event": event, "ref": ref, "replayed": False}

    def evaluate_schedule(self, covers, *, day, profile,
                          channel="email", lang="en"):
        """Time-driven triggers over the cover book: pre-travel
        reminder, expiry warning at the profile's expiring-soon mark,
        extension nudge inside the grace window. Covers already closed
        by a departure scan are done talking."""
        emitted = []
        for cover in covers:
            if cover.get("departed_day") is not None:
                continue  # the trip is closed — silence, correctly
            win = cover["window"]
            ref = cover["ref"]
            if win["from"] - day == self.rules["pre_travel_days"]:
                emitted.append(self.emit(
                    "pre-travel-reminder", ref=ref, channel=channel,
                    lang=lang, day=day,
                    detail={"starts_day": win["from"]}))
            if win["to"] - day == profile["expiring_soon_days"]:
                emitted.append(self.emit(
                    "expiry-warning", ref=ref, channel=channel,
                    lang=lang, day=day,
                    detail={"expires_day": win["to"]}))
            if win["to"] < day <= win["to"] + profile["grace_days"]:
                emitted.append(self.emit(
                    "extension-nudge", ref=ref, channel=channel,
                    lang=lang, day=day,
                    detail={"lapsed_day": win["to"],
                            "grace_until":
                                win["to"] + profile["grace_days"]}))
        return emitted

    def report(self):
        """Denominators always visible."""
        per_event = {}
        for rec in self.outbox:
            per_event[rec["event"]] = per_event.get(rec["event"], 0) + 1
        return {"sent": len(self.outbox), "by_event": per_event,
                "channels": sorted({r["channel"] for r in self.outbox})}
