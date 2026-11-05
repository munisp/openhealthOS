"""vhease.py — the EASE single-rail adapter: CV-VHC rides the EASE
(Zetes Fastrace) border platform as a composed service boundary — the
transport is injected, nothing proprietary ships, and an adapter
without a transport refuses rather than pretending to be a rail.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §8 INT-01):
  - INT-01: EASE integration — cover issuance, exemption status, and
    verification events flow over the single rail.

Doctrines:
- Composed, never vendored: the rail client is a deployment-supplied
  callable; this module is the shape, not the SDK.
- A cable not plugged in is not an interface: no transport, no adapter.
- Every message carries an idempotency key; the rail must be safe to
  retry.
- Rail failures refuse loudly; a swallowed 500 is a traveller who
  'exists' only on our side.
"""

import hashlib
import json
import time

VHEASE_VERSION = "vhc-ease@1.0.0"

MESSAGE_TYPES = ("cover-issued", "cover-revoked", "exemption-issued",
                 "verification-event", "manifest-batch")


class EaseError(Exception):
    pass


def _refuse(msg):
    raise EaseError(msg)


class EaseAdapter:
    """The single rail. `transport` is a callable taking
    (message_type, payload) and returning a dict ack; inject the real
    EASE client at deployment."""

    def __init__(self, transport=None, *, rail_version="ease@1.0"):
        if transport is None:
            _refuse("no transport — a cable not plugged in is not an "
                    "interface")
        if not callable(transport):
            _refuse("transport must be callable — the rail is composed, "
                    "never vendored")
        self.transport = transport
        self.rail_version = rail_version
        self.sent = []        # append-only outbox
        self.audit = []

    def _send(self, message_type, payload):
        if message_type not in MESSAGE_TYPES:
            _refuse("unknown message type — the rail's vocabulary is "
                    "versioned")
        envelope = {
            "type": message_type,
            "payload": payload,
            "rail_version": self.rail_version,
            "idem_key": hashlib.sha256(
                json.dumps({"t": message_type, "p": payload},
                           sort_keys=True).encode()).hexdigest(),
            "sent_at": time.time(),
        }
        try:
            ack = self.transport(message_type, envelope)
        except Exception as exc:
            _refuse("rail failure on %s: %s — refused loudly, not "
                    "swallowed" % (message_type, exc))
        if not isinstance(ack, dict) or ack.get("status") != "accepted":
            _refuse("rail did not accept %s — the traveller exists on "
                    "our side only if the rail says so" % message_type)
        self.sent.append(envelope)
        self.audit.append({"event": "rail_sent", "type": message_type,
                           "idem_key": envelope["idem_key"]})
        return ack

    # --- typed doors ----------------------------------------------------
    def cover_issued(self, cover):
        """The border learns of a new cover the moment it exists."""
        return self._send("cover-issued", {
            "ref": cover["ref"],
            "passport_sha256": cover["passport_sha256"],
            "window": cover["window"],
            "profile_version": cover["profile_version"]})

    def cover_revoked(self, ref, *, reason, day):
        return self._send("cover-revoked",
                          {"ref": ref, "reason": reason, "day": day})

    def exemption_issued(self, exemption):
        return self._send("exemption-issued", {
            "ref": exemption["ref"],
            "passport_sha256": exemption["passport_sha256"],
            "category": exemption["category"],
            "expires_day": exemption["expires_day"]})

    def verification_event(self, verdict, *, lane, day):
        """Every border verdict rides back to the rail — the rail's
        statistics are only as honest as what we send it."""
        if not lane:
            _refuse("a verification event names its lane")
        return self._send("verification-event", {
            "ref": verdict.get("ref"), "verdict": verdict["verdict"],
            "reasons": verdict["reasons"], "lane": lane, "day": day,
            "mode": verdict["evidence"]["mode"],
            "latency_ms": verdict["evidence"]["latency_ms"]})

    def manifest_batch(self, group):
        return self._send("manifest-batch", {
            "group_ref": group["group_ref"], "count": group["count"],
            "refs": [c["ref"] for c in group["covers"]]})

    def report(self):
        return {"rail_version": self.rail_version,
                "messages_sent": len(self.sent),
                "by_type": {t: sum(1 for e in self.sent
                                   if e["type"] == t)
                            for t in MESSAGE_TYPES}}
