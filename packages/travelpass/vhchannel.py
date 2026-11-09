"""USSD / WhatsApp lane — zero-data-channel twin of the QR flow.

The border and the buyer do not always have a smartphone or data. The
lane is a state machine over telco entries: each market's telcos are
registered (from vhmarkets), each channel verb is a menu step, and an
unregistered telco refuses — a lane without a registry is not a lane.

Verification by short code: the officer dials the shortcode, enters
the cover ref, and the engine answers green/amber/red over the same
verdict path as the QR — one verdict pipeline, two front doors.
"""

from vhmarkets import market as _market

CHANNEL_VERSION = "vhchannel@1.1.0"

# session states — the vocabulary is versioned
STATES = ("idle", "menu", "buy_days", "buy_age", "buy_confirm",
          "verify_ref", "done")

MENUS = {
    "main": ("1. Buy cover", "2. Verify cover", "3. My pass"),
}


class ChannelError(Exception):
    pass


def _refuse(msg):
    raise ChannelError(msg)


class LaneSession:
    """One USSD/WhatsApp session. Every transition is state-checked —
    a session that jumps the queue is a fraud shape."""

    def __init__(self, session_id, *, telco, country, day):
        mk = _market(country)                # unknown market refuses
        if telco not in mk["telcos"]:
            _refuse(f"telco {telco!r} not registered for {country} — "
                    "a lane without a registry is not a lane")
        self.session_id = session_id
        self.telco = telco
        self.country = country
        self.day = day
        self.state = "idle"
        self.draft = {}
        self.transcript = []

    def _go(self, to):
        legal = {
            "idle": ("menu",),
            "menu": ("buy_days", "verify_ref", "done"),
            "buy_days": ("buy_age",),
            "buy_age": ("buy_confirm",),
            "buy_confirm": ("done",),
            "verify_ref": ("done",),
            "done": ("idle",),
        }
        if to not in legal[self.state]:
            _refuse(f"illegal transition {self.state}->{to} — "
                    "a session that jumps the queue is a fraud shape")
        self.state = to

    def handle(self, text):
        self.transcript.append({"state": self.state, "in": text})
        if self.state == "idle":
            self._go("menu")
            return "\n".join(MENUS["main"])
        if self.state == "menu":
            if text == "1":
                self._go("buy_days")
                return "Days of cover? (1-90)"
            if text == "2":
                self._go("verify_ref")
                return "Enter cover reference (VHC-...)"
            _refuse("unknown menu choice — the menu is the contract")
        if self.state == "buy_days":
            days = int(text) if text.isdigit() else 0
            if not 1 <= days <= 90:
                _refuse("days outside 1-90 — beyond the cap is residence")
            self.draft["days"] = days
            self._go("buy_age")
            return "Traveller age?"
        if self.state == "buy_age":
            if not text.isdigit():
                _refuse("traveller age is required — absence is not a discount")
            self.draft["age"] = int(text)
            self._go("buy_confirm")
            return ("Confirm: %d days, age %d — 1 to confirm"
                    % (self.draft["days"], self.draft["age"]))
        if self.state == "buy_confirm":
            if text != "1":
                self._go("done")
                return "Cancelled — stated, not vanished"
            self._go("done")
            return ("Quote issued — pay via mobile money to activate. "
                    "(Purchase completes through the engine's own door.)")
        if self.state == "verify_ref":
            self.draft["verify_ref"] = text.strip()
            self._go("done")
            return "verified"       # the caller renders the verdict
        _refuse("session done — dial again")

    # -- resumption: the telco drops sessions at 30–60s; the menu state
    # must survive the redial. A snapshot is HMAC-signed and single-use
    # through the wire package's ResumeTokenIssuer — the lane signs the
    # state, the wire guards the replay.

    def snapshot(self):
        """Export the exact resumable state. Done-state sessions do not
        resume — a finished transaction is finished."""
        if self.state == "done":
            _refuse("a finished session does not resume — stated")
        return {"session_id": self.session_id, "telco": self.telco,
                "country": self.country, "day": self.day,
                "state": self.state, "draft": dict(self.draft),
                "transcript": list(self.transcript)}

    @classmethod
    def restore(cls, snap):
        """Restore from a snapshot produced by snapshot(). The wire layer
        has already verified the token; here we validate shape, never
        trust."""
        for key in ("session_id", "telco", "country", "day", "state", "draft"):
            if key not in snap:
                _refuse(f"snapshot missing {key} — partial state is not a session")
        if snap["state"] not in STATES or snap["state"] == "done":
            _refuse(f"snapshot state {snap['state']!r} cannot resume — stated")
        s = cls(snap["session_id"], telco=snap["telco"],
                country=snap["country"], day=snap["day"])
        s.state = snap["state"]
        s.draft = dict(snap["draft"])
        s.transcript = list(snap.get("transcript", []))
        s.transcript.append({"state": snap["state"],
                             "in": "<resumed after telco drop — stated>"})
        return s


def verify_by_code(vreg, *, cover_ref, day, lane):
    """Officer verification over the lane — the same verdict pipeline
    as the QR, reached by shortcode. Unknown ref: RED, stated."""
    try:
        verdict = vreg.verify(cover_ref=cover_ref, day=day, lane=lane)
    except Exception:
        return {"verdict": "red", "reason": "unknown or invalid ref — "
                "RED is a decision, not an error"}
    return verdict


def report():
    return {"version": CHANNEL_VERSION, "states": len(STATES)}
