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

1.1.0 — the production rail residual, closed:
  - DURABLE OUTBOX: an envelope is persisted (SQLite, WAL) BEFORE the
    wire is touched and marked sent only after the rail accepts. A
    restart replays pending envelopes with the SAME idempotency key —
    the rail dedupes, the traveller is never sent twice.
  - DEAD-LETTER: after a declared max_attempts a pending envelope goes
    dead WITH its last error — stated, never silently dropped.
  - INJECTABLE CLOCK: sent_at comes from the injected clock.
"""

import hashlib
import json
import sqlite3
import time

VHEASE_VERSION = "vhc-ease@1.1.0"

MESSAGE_TYPES = ("cover-issued", "cover-revoked", "exemption-issued",
                 "verification-event", "manifest-batch")

MAX_ATTEMPTS = 8


class EaseError(Exception):
    pass


def _refuse(msg):
    raise EaseError(msg)


class SqliteOutbox:
    """The envelope journal. Persist-before-send; the row IS the truth
    of what the rail was asked to carry."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS outbox ("
            " idem_key TEXT PRIMARY KEY,"
            " type TEXT NOT NULL,"
            " envelope TEXT NOT NULL,"
            " attempts INTEGER NOT NULL DEFAULT 0,"
            " status TEXT NOT NULL DEFAULT 'pending',"
            " last_error TEXT)")
        self.db.commit()

    def stage(self, envelope):
        self.db.execute(
            "INSERT OR IGNORE INTO outbox (idem_key, type, envelope)"
            " VALUES (?, ?, ?)",
            (envelope["idem_key"], envelope["type"],
             json.dumps(envelope, sort_keys=True)))
        self.db.commit()

    def mark_sent(self, idem_key):
        self.db.execute("UPDATE outbox SET status='sent' WHERE idem_key=?",
                        (idem_key,))
        self.db.commit()

    def fail(self, idem_key, error, *, max_attempts=MAX_ATTEMPTS):
        row = self.db.execute(
            "SELECT attempts FROM outbox WHERE idem_key=?",
            (idem_key,)).fetchone()
        attempts = (row[0] if row else 0) + 1
        status = "dead" if attempts >= max_attempts else "pending"
        self.db.execute(
            "UPDATE outbox SET attempts=?, status=?, last_error=?"
            " WHERE idem_key=?",
            (attempts, status, error, idem_key))
        self.db.commit()
        return status

    def pending(self):
        return [json.loads(r[0]) for r in self.db.execute(
            "SELECT envelope FROM outbox WHERE status='pending'"
            " ORDER BY rowid")]

    def dead(self):
        return [{"idem_key": r[0], "last_error": r[1]}
                for r in self.db.execute(
                    "SELECT idem_key, last_error FROM outbox"
                    " WHERE status='dead'")]

    def close(self):
        self.db.close()


class EaseAdapter:
    """The single rail. `transport` is a callable taking
    (message_type, payload) and returning a dict ack; inject the real
    EASE client at deployment. `outbox` makes the rail restart-safe;
    without one the adapter is volatile and SAYS SO in report()."""

    def __init__(self, transport=None, *, rail_version="ease@1.0",
                 outbox=None, now=None):
        if transport is None:
            _refuse("no transport — a cable not plugged in is not an "
                    "interface")
        if not callable(transport):
            _refuse("transport must be callable — the rail is composed, "
                    "never vendored")
        self.transport = transport
        self.rail_version = rail_version
        self.outbox = outbox  # None => volatile, stated in report()
        self.now = now or time.time
        self.sent = []        # append-only outbox (session view)
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
            "sent_at": self.now(),
        }
        if self.outbox is not None:
            self.outbox.stage(envelope)  # persist BEFORE the wire
        try:
            ack = self.transport(message_type, envelope)
        except Exception as exc:
            if self.outbox is not None:
                status = self.outbox.fail(envelope["idem_key"], str(exc))
                _refuse("rail failure on %s: %s — envelope journaled "
                        "(%s), replay will carry the same idempotency "
                        "key" % (message_type, exc, status))
            _refuse("rail failure on %s: %s — refused loudly, not "
                    "swallowed" % (message_type, exc))
        if not isinstance(ack, dict) or ack.get("status") != "accepted":
            if self.outbox is not None:
                status = self.outbox.fail(envelope["idem_key"],
                                          "rail did not accept")
                _refuse("rail did not accept %s — journaled (%s), the "
                        "traveller exists on our side only if the rail "
                        "says so" % (message_type, status))
            _refuse("rail did not accept %s — the traveller exists on "
                    "our side only if the rail says so" % message_type)
        if self.outbox is not None:
            self.outbox.mark_sent(envelope["idem_key"])
        self.sent.append(envelope)
        self.audit.append({"event": "rail_sent", "type": message_type,
                           "idem_key": envelope["idem_key"]})
        return ack

    def replay_pending(self):
        """After a restart: re-send journaled-but-unacked envelopes.
        The idempotency key is recomputed-identical, so a rail that DID
        receive the first attempt dedupes the replay. Returns counts."""
        if self.outbox is None:
            _refuse("no outbox — nothing durable to replay; a volatile "
                    "adapter cannot promise restart safety")
        sent, dead = 0, 0
        for envelope in self.outbox.pending():
            try:
                ack = self.transport(envelope["type"], envelope)
            except Exception as exc:
                if self.outbox.fail(envelope["idem_key"], str(exc)) == "dead":
                    dead += 1
                continue
            if isinstance(ack, dict) and ack.get("status") == "accepted":
                self.outbox.mark_sent(envelope["idem_key"])
                self.sent.append(envelope)
                self.audit.append({"event": "rail_replayed",
                                   "type": envelope["type"],
                                   "idem_key": envelope["idem_key"]})
                sent += 1
            else:
                if self.outbox.fail(envelope["idem_key"],
                                    "rail did not accept") == "dead":
                    dead += 1
        return {"replayed": sent, "dead": dead}

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
        out = {"rail_version": self.rail_version,
               "version": VHEASE_VERSION,
               "messages_sent": len(self.sent),
               "durability": ("sqlite-outbox" if self.outbox is not None
                              else "VOLATILE — restart loses unacked "
                                   "envelopes; stated, not hidden"),
               "by_type": {t: sum(1 for e in self.sent
                                  if e["type"] == t)
                           for t in MESSAGE_TYPES}}
        if self.outbox is not None:
            out["pending"] = len(self.outbox.pending())
            out["dead"] = self.outbox.dead()
        return out


def http_transport(endpoint, *, token, opener=None):
    """The production rail: POST each envelope to the EASE endpoint as
    JSON, return the ack. `opener` is the injected HTTP layer (urllib at
    deployment, a stub in tests) — the rail is composed, never vendored.
    No endpoint: refuse — a cable not plugged in is not an interface."""
    if not endpoint:
        _refuse("no endpoint — a cable not plugged in is not an interface")
    if not endpoint.startswith("https://"):
        _refuse("the rail speaks TLS or it does not speak")
    if opener is None:
        import urllib.request
        def opener(url, body, headers):
            req = urllib.request.Request(
                url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())

    def transport(message_type, envelope):
        body = json.dumps(envelope).encode()
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer " + token,
                   "X-Rail-Version": envelope["rail_version"]}
        return opener(endpoint, body, headers)
    return transport
