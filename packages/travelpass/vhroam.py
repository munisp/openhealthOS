"""Roaming cover — one policy, many countries, composed not coded.

A roaming profile is a COMPOSITION of member country profiles: the
traveller buys once, the engine prices each country's segment from its
own versioned profile, and a verdict anywhere is the strictest member
rule that applies. ECOWAS-15 ships as an entry; the Cape Verde
archipelago participates as a member like any mainland state.

Doctrine: members are entries. Removing a country from the roam is a
new profile version, never an if-branch.
"""

from vhcover import VHC_PROFILES, price_cover

ROAM_VERSION = "vhroam@1.0.0"

# ECOWAS membership as data (15 states). Cabo Verde is member 15 —
# the archipelago is in the corridor, not an afterthought.
ECOWAS_MEMBERS = ("bj", "bf", "cv", "ci", "gm", "gh", "gn", "gw",
                  "lr", "ml", "ne", "ng", "sn", "sl", "tg")

_PROFILES_BY_COUNTRY = {p["country"]: pid
                        for pid, p in VHC_PROFILES.items()}


class RoamError(Exception):
    pass


def _refuse(msg):
    raise RoamError(msg)


def compose_roam_profile(profile_id, *, countries, label):
    """A composite profile naming its member country profiles."""
    members = []
    for c in countries:
        pid = _PROFILES_BY_COUNTRY.get(c)
        if pid is None:
            _refuse(f"no country profile for {c!r} — a roam member is a "
                    "profile entry, not a flag")
        members.append(pid)
    return {"profile_id": profile_id, "label": label,
            "members": tuple(members), "countries": tuple(countries),
            "version": ROAM_VERSION}


def price_roam(roam, *, segments, age_years):
    """segments: {country: days}. Each segment prices from its own
    member profile; template members refuse (unratified stays
    unsellable). Returns per-segment arithmetic so the receipt can
    show its work."""
    lines, total = [], {}
    for country, days in sorted(segments.items()):
        pid = _PROFILES_BY_COUNTRY.get(country)
        if pid is None or pid not in roam["members"]:
            _refuse(f"{country!r} is not in this roam — the corridor "
                    "is the contract")
        p = VHC_PROFILES[pid]
        minor, units = price_cover(p, days=days, age_years=age_years)
        lines.append({"country": country, "profile": pid, "days": days,
                      "minor": minor, "currency": p["currency"]})
        cur = p["currency"]
        total[cur] = total.get(cur, 0) + minor
    return {"lines": lines, "totals_by_currency": total,
            "mixed_currency": len(total) > 1}


def verdict_rule(roam, member_verdicts):
    """Strictest wins: any member red -> red; else any amber -> amber.
    A corridor is only as green as its strictest member."""
    if any(v == "red" for v in member_verdicts):
        return "red"
    if any(v == "amber" for v in member_verdicts):
        return "amber"
    if not member_verdicts:
        _refuse("no member verdicts — absence is not green")
    return "green"


def ecowas_roam():
    return compose_roam_profile(
        "ecowas-roam@1.0.0",
        countries=[c for c in ECOWAS_MEMBERS if c in _PROFILES_BY_COUNTRY],
        label="ECOWAS roaming cover — one policy, fifteen states")


def report():
    return {"version": ROAM_VERSION,
            "ecowas_members": len(ECOWAS_MEMBERS),
            "ecowas_profiled": len([c for c in ECOWAS_MEMBERS
                                    if c in _PROFILES_BY_COUNTRY])}
