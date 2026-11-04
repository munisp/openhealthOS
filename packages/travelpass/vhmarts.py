"""vhmarts.py — the actuarial data marts: premiums against claims,
frequency against covered days, severity per claim — the numbers the
scheme prices itself by, every one with its denominator.

Spec coverage (VHC-class, e.g. CV-VHC-PLAT-SPEC §6.6):
  - FR-REG-04: actuarial data marts — loss ratio, frequency, severity,
    per site and syndromic class, computed from the incident registry
    and the claims ledger, never typed in.

Doctrines:
- A ratio without a denominator is a slogan.
- The mart is derived from the ledgers on every call — a mart you can
  edit is a mart nobody prices from.
"""

VHMARTS_VERSION = "vhc-marts@1.0.0"


def actuarial_mart(*, vhc, claims, incidents, through_day):
    """Build the mart from the three ledgers. `vhc` is the cover
    registry (premiums + covered days), `claims` the claims registry,
    `incidents` the incident registry."""
    premiums = sum(p["minor"] for p in vhc.payments
                   if p["kind"] in ("premium", "extension"))
    covered_days = sum(c["window"]["to"] - c["window"]["from"] + 1
                       for c in vhc.covers.values()
                       if c["window"]["from"] <= through_day)
    paid = [c for c in claims.cases.values() if c["payment"]]
    paid_minor = sum(c["payment"]["net_minor"] for c in paid)
    in_window = [i for i in incidents.incidents.values()
                 if i["day"] <= through_day]
    per_class = {}
    for inc in in_window:
        cell = per_class.setdefault(
            inc["syndromic_class"], {"incidents": 0, "cost_minor": 0})
        cell["incidents"] += 1
        cell["cost_minor"] += inc["cost_minor"]
    per_site = {}
    for inc in in_window:
        cell = per_site.setdefault(
            inc["site"], {"incidents": 0, "cost_minor": 0})
        cell["incidents"] += 1
        cell["cost_minor"] += inc["cost_minor"]
    return {
        "through_day": through_day,
        "premiums_minor": premiums,
        "claims_paid_minor": paid_minor,
        "loss_ratio": (round(paid_minor / premiums, 4)
                       if premiums else None),
        "covered_days": covered_days,
        "claims_count": len(paid),
        "frequency_per_1000_days": (
            round(len(paid) * 1000 / covered_days, 2)
            if covered_days else None),
        "severity_minor": (round(paid_minor / len(paid))
                           if paid else None),
        "incidents_by_class": per_class,
        "incidents_by_site": per_site,
        "incidents_total": len(in_window),
        "note": ("derived from ledgers at call time — not editable"
                 if premiums else
                 "no premiums yet — ratios withheld, denominators "
                 "shown"),
    }
