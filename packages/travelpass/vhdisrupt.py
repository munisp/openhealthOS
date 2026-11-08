"""vhdisrupt.py — the airline-disruption API.

The OTA widget embedded cover at booking; this API inverts the flow:
when an airline posts a delay, the airline's own notification carries
OUR cover-and-care verdict — "your cover extends automatically, a
parametric payout of X is already posted, here is your care line."
The disruption stops being an apology and becomes a service.

Doctrine:
- AIRLINES ARE ORIGIN-BOUND PARTNERS: an API token is bound to a
  declared airline + origin; a token presented from the wrong origin
  refuses (widget doctrine carried to the API).
- VERDICTS COME FROM ATTESTED EVENTS: the API answers from the
  RiderEngine's attested disruptions — an unattested delay claim gets
  a stated "no attested event", never a fabricated payout.
- EVERY RESPONSE IS CHECKSUMMED: the airline embeds the verdict;
  the checksum is the contract between their app and ours.
- RATE LIMITS ARE DECLARED: per-token bucket; saturation refuses
  with a stated reason.
"""

import hashlib
import json
import secrets

VHDISRUPT_VERSION = "vhdisrupt@1.0.0"


class DisruptError(Exception):
    pass


def _refuse(msg):
    raise DisruptError(msg)


class DisruptionAPI:
    def __init__(self, rider_engine, *, burst=10):
        if burst < 1:
            _refuse("the rate limit is a declared positive bucket")
        self.engine = rider_engine
        self.burst = burst
        self.tokens = {}           # token -> {airline, origin}
        self.allowance = {}        # token -> remaining in window
        self.responses = 0

    def issue_token(self, *, airline, origin, by, day):
        if not airline or not origin:
            _refuse("a token names its airline and its origin")
        if not origin.startswith("https://"):
            _refuse("origins are https — a plaintext partner is a "
                    "leak")
        token = "vhd_" + secrets.token_hex(16)
        self.tokens[token] = {"airline": airline, "origin": origin,
                              "issued_by": by, "issued_day": day}
        self.allowance[token] = self.burst
        return token

    def _guard(self, token, origin):
        t = self.tokens.get(token)
        if t is None:
            _refuse("unknown token — stated, not served")
        if t["origin"] != origin:
            _refuse(f"token bound to {t['origin']}, presented from "
                    f"{origin} — origin binding refuses")
        if self.allowance[token] < 1:
            _refuse("rate limit saturated — stated backpressure; the "
                    "window refills")
        self.allowance[token] -= 1
        return t

    def refill_window(self):
        for t in self.allowance:
            self.allowance[t] = self.burst

    def passenger_verdict(self, token, *, origin, flight, cover_ref,
                          day):
        """The airline asks: what do we tell this passenger?"""
        self._guard(token, origin)
        self.responses += 1
        ev = next((e for e in reversed(self.engine.events)
                   if e["flight"] == flight), None)
        if ev is None:
            body = {"flight": flight, "cover_ref": cover_ref,
                    "state": "no-attested-event",
                    "message": "no attested disruption for this "
                               "flight — stated, not fabricated"}
        else:
            payout = next(
                (p for p in self.engine.payouts
                 if p["cover_ref"] == cover_ref
                 and p["evidence_sha256"] == ev["event_sha256"]),
                None)
            if payout is None:
                body = {"flight": flight, "cover_ref": cover_ref,
                        "state": "attested-below-floor",
                        "delay_minutes": ev["delay_minutes"],
                        "message": f"delay of {ev['delay_minutes']} "
                                   "min attested; below the "
                                   "parametric floor — cover extends, "
                                   "no payout, stated"}
            else:
                body = {"flight": flight, "cover_ref": cover_ref,
                        "state": "payout-posted",
                        "delay_minutes": ev["delay_minutes"],
                        "payout_minor": payout["payout_minor"],
                        "currency": payout["currency"],
                        "message": f"delay of {ev['delay_minutes']} "
                                   "min attested — your cover extends "
                                   "automatically and a payout of "
                                   f"{payout['payout_minor']} "
                                   f"{payout['currency']} minor units "
                                   "is already posted",
                        "evidence_sha256": ev["event_sha256"]}
        canon = json.dumps(body, sort_keys=True,
                           ensure_ascii=False).encode()
        body["sha256"] = hashlib.sha256(canon).hexdigest()
        body["version"] = VHDISRUPT_VERSION
        return body

    def report(self):
        return {"version": VHDISRUPT_VERSION,
                "tokens": len(self.tokens),
                "responses": self.responses}
