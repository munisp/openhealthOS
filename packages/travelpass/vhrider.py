"""Parametric disruption rider — the oracle attests, the engine pays.

Flight delayed or cancelled beyond a stated threshold: the courtesy
extension (S-029) and a micro-payout trigger automatically — no claims
filing, no adjuster, no dispute. The oracle feed is an attested entry;
an unattested event refuses, because a payout without an attestation
is a leak with a smile.

The payout posts through the fund's own ledger; this module decides
WHETHER and HOW MUCH, never where the money sits.
"""

import hashlib

RIDER_VERSION = "vhrider@1.0.0"

# Attested disruption oracles — an entry per feed. signature_key_id is
# the KeyRing id the feed's attestations verify against.
ORACLES = {}

# Parametric table — delay minutes -> payout in the cover profile's
# minor units, as a fraction of the block/day price. Entries, not
# branches.
PAYOUT_TABLE = (
    {"min_delay_minutes": 240, "factor": 1.0},    # 4h+: full block/day
    {"min_delay_minutes": 120, "factor": 0.5},    # 2h+: half
)


class RiderError(Exception):
    pass


def _refuse(msg):
    raise RiderError(msg)


def register_oracle(oracle_id, *, signature_key_id, source):
    if not oracle_id or not signature_key_id or not source:
        _refuse("an oracle names itself, its key, and its source")
    ORACLES[oracle_id] = {"signature_key_id": signature_key_id,
                          "source": source}
    return oracle_id


def payout_for_delay(delay_minutes, *, unit_price_minor):
    for row in PAYOUT_TABLE:                      # table is ordered desc
        if delay_minutes >= row["min_delay_minutes"]:
            return int(round(unit_price_minor * row["factor"]))
    return 0


class RiderEngine:
    def __init__(self, *, key_ring):
        self.key_ring = key_ring
        self.events = []          # append-only attested events
        self.payouts = []         # append-only payout decisions

    def attest(self, oracle_id, *, flight, disruption, delay_minutes,
               signature, day):
        """Verify the oracle's signature before anything moves."""
        o = ORACLES.get(oracle_id)
        if o is None:
            _refuse(f"unknown oracle {oracle_id!r} — unattested events "
                    "pay nothing")
        payload = f"{flight}|{disruption}|{delay_minutes}|{day}".encode()
        key = self.key_ring.keys.get(o["signature_key_id"])
        if key is None:
            _refuse("oracle key not in the ring — custody is the boundary")
        if key.get("verifier"):
            ok = bool(key["verifier"](payload, signature))
        elif key.get("secret"):
            import hmac
            expect = hmac.new(key["secret"].encode(), payload,
                              hashlib.sha256).hexdigest().encode()
            ok = hmac.compare_digest(expect, signature)
        else:
            _refuse("oracle key can neither verify nor be verified — "
                    "custody is the boundary")
        if not ok:
            _refuse("oracle signature failed — a payout without an "
                    "attestation is a leak with a smile")
        ev = {"oracle": oracle_id, "flight": flight,
              "disruption": disruption, "delay_minutes": delay_minutes,
              "day": day,
              "event_sha256": hashlib.sha256(payload).hexdigest()}
        self.events.append(ev)
        return dict(ev)

    def trigger(self, vhc, *, cover_ref, event_sha256, day):
        """The parametric trigger: courtesy extension + micro-payout,
        posted with the attestation as evidence."""
        ev = next((e for e in self.events
                   if e["event_sha256"] == event_sha256), None)
        if ev is None:
            _refuse("no attested event — parametric means attested, "
                    "not asserted")
        cover = vhc.covers.get(cover_ref)
        if cover is None:
            _refuse("unknown cover")
        unit = cover["quote"]["total_minor"]
        payout = payout_for_delay(ev["delay_minutes"],
                                  unit_price_minor=unit)
        if payout <= 0:
            return {"cover_ref": cover_ref, "payout_minor": 0,
                    "reason": "below the parametric floor — stated"}
        decision = {"cover_ref": cover_ref, "payout_minor": payout,
                    "currency": vhc.profile["currency"],
                    "evidence_sha256": event_sha256, "day": day,
                    "kind": "parametric_disruption"}
        self.payouts.append(decision)
        return dict(decision)

    def report(self):
        return {"version": RIDER_VERSION, "oracles": len(ORACLES),
                "events": len(self.events), "payouts": len(self.payouts)}
