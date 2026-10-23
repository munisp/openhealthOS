"""router.py — right referral, first time.

Patients bounce hospital to hospital hunting for capability. The router
matches a case's NEED to a facility that actually HAS it — registry-
driven, honest about staleness: a capability attested more than
max_age days ago is treated as unverified, because 'they had a working
CT in 2019' is how patients die in transit.

Doctrine:
- Need matching is exact and declared (no fuzzy 'probably has xray').
- Stale attestations degrade to unverified — reported, not hidden.
- No capable facility found is an honest empty result with the nearest
  FALLBACK tier (state general), never a fabricated match.
"""

STALE_DAYS = 90


class CapabilityRegistry:
    def __init__(self):
        self._caps = {}  # facility -> {capability: attested_at_day}

    def attest(self, facility, capability, day):
        self._caps.setdefault(facility, {})[capability] = day

    def capable(self, capability, today, max_age=STALE_DAYS):
        ok, stale = [], []
        for f, caps in sorted(self._caps.items()):
            att = caps.get(capability)
            if att is None:
                continue
            (ok if today - att <= max_age else stale).append(f)
        return {"capable": ok, "unverified_stale": stale}


def route(need, registry, today, fallback=None):
    r = registry.capable(need, today)
    if r["capable"]:
        return {"ok": True, "facility": r["capable"][0],
                "alternates": r["capable"][1:],
                "stale_noted": r["unverified_stale"]}
    return {"ok": False, "reason": "no-verified-capability",
            "fallback": fallback, "stale_noted": r["unverified_stale"]}
