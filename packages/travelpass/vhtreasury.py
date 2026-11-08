"""Treasury — multi-currency settlement totals, peg-aware revaluation,
FX exposure per corridor. Finance sees the exposure before it is a
surprise.

Doctrine: mixed-currency sums refuse (settlement doctrine). Revaluation
reads rates from versioned peg entries, never hardcoded. A corridor
without a stated rate is reported as unpriced exposure, not zero.
"""

from vhmarkets import currency as _currency

TREASURY_VERSION = "vhtreasury@1.1.0"

# Peg/mid-rate entries — versioned data. rate = units of currency per
# 1 EUR (the scheme's reporting currency). A rate is a claim with a
# source and a day; stale rates are reported, not silently used.
RATES = {
    "CVE": {"per_eur": 110.265, "kind": "peg", "source": "BCV", "day": 0},
    "NGN": {"per_eur": 1650.0, "kind": "indicative", "source": "operator",
            "day": 0},
    "USD": {"per_eur": 1.08, "kind": "indicative", "source": "operator",
            "day": 0},
    "GHS": {"per_eur": 16.5, "kind": "indicative", "source": "operator",
            "day": 0},
    "KES": {"per_eur": 140.0, "kind": "indicative", "source": "operator",
            "day": 0},
    "ZAR": {"per_eur": 20.0, "kind": "indicative", "source": "operator",
            "day": 0},
}


class TreasuryError(Exception):
    pass


def _refuse(msg):
    raise TreasuryError(msg)


def register_rate(code, *, per_eur, kind, source, day):
    _currency(code)
    if per_eur <= 0 or not source:
        _refuse("a rate is a positive number with a named source")
    RATES[code] = {"per_eur": per_eur, "kind": kind,
                   "source": source, "day": day}
    return dict(RATES[code])


def eur_value_minor(code, amount_minor):
    """Revalue minor units into EUR minor via the stated rate.
    Unknown rate: refuse — an unstated rate is not zero exposure."""
    _currency(code)
    if code == "EUR":
        return amount_minor
    r = RATES.get(code)
    if r is None:
        _refuse(f"no rate for {code} — unpriced exposure is stated, "
                "never zeroed")
    scale = _currency(code)["scale"]
    eur_major = (amount_minor / scale) / r["per_eur"]
    return int(round(eur_major * 100))


def corridor_report(settlement_lines):
    """Group settlement lines (payer/amount_minor/currency/ref + optional
    corridor) into per-currency, per-corridor exposure with EUR
    revaluation. Mixed-currency arithmetic never happens silently."""
    totals = {}
    for ln in settlement_lines:
        code = ln["currency"]
        _currency(code)
        corr = ln.get("corridor", "unstated")
        bucket = totals.setdefault(
            (corr, code), {"corridor": corr, "currency": code,
                           "minor": 0, "lines": 0})
        bucket["minor"] += ln["amount_minor"]
        bucket["lines"] += 1
    out = []
    for b in sorted(totals.values(),
                    key=lambda b: (b["corridor"], b["currency"])):
        row = dict(b)
        try:
            row["eur_minor"] = eur_value_minor(b["currency"], b["minor"])
            row["rate_kind"] = (RATES.get(b["currency"]) or
                                {"kind": "native"}).get("kind", "peg")
        except TreasuryError:
            row["eur_minor"] = None      # unpriced exposure, stated
            row["rate_kind"] = "unpriced"
        out.append(row)
    return out


def report(settlement_lines):
    rows = corridor_report(settlement_lines)
    priced = [r for r in rows if r["eur_minor"] is not None]
    return {"version": TREASURY_VERSION,
            "corridors": len({r["corridor"] for r in rows}),
            "currencies": sorted({r["currency"] for r in rows}),
            "total_eur_minor": sum(r["eur_minor"] for r in priced),
            "unpriced": [r for r in rows if r["eur_minor"] is None]}


# ── Rate feeds (vhtreasury@1.1.0) ─────────────────────────────────
# The static registry above prices a corridor forever; a live treasury
# reads FEEDS. A feed is an injected, named rate source with a declared
# freshness budget: a rate older than its budget is stale, and a stale
# rate REFUSES to price — it reports, it never silently converts.
# Pegged rates (CVE/EUR is statutory) are exempt by declared kind,
# stated in the report, not assumed.

class RateFeed:
    """An injected rate source with a declared staleness budget."""

    def __init__(self, source, *, max_age_days):
        if not source:
            _refuse("a feed names its source — anonymous rates refuse")
        if max_age_days < 1:
            _refuse("a freshness budget is a declared positive number "
                    "of days")
        self.source = source
        self.max_age_days = max_age_days
        self.rates = {}          # code -> {per_eur, feed_day}

    def update(self, code, *, per_eur, feed_day):
        _currency(code)
        if per_eur <= 0:
            _refuse("a feed rate is a positive number")
        self.rates[code] = {"per_eur": per_eur, "feed_day": feed_day}
        return dict(self.rates[code])

    def age_days(self, code, *, today):
        r = self.rates.get(code)
        if r is None:
            _refuse(f"feed {self.source} has no rate for {code} — "
                    "unpriced is stated, never zeroed")
        return today - r["feed_day"]

    def is_stale(self, code, *, today):
        return self.age_days(code, today=today) > self.max_age_days

    def eur_value_minor(self, code, amount_minor, *, today):
        """Revalue through the feed. Stale refuses; a peg in the
        static registry is the declared fallback, stated as such."""
        _currency(code)
        if code == "EUR":
            return amount_minor
        if code in self.rates:
            if self.is_stale(code, today=today):
                _refuse(f"{code} rate from {self.source} is "
                        f"{self.age_days(code, today=today)}d old "
                        f"against a {self.max_age_days}d budget — "
                        "stale rates report, they do not convert")
            scale = _currency(code)["scale"]
            eur_major = (amount_minor / scale) / \
                self.rates[code]["per_eur"]
            return int(round(eur_major * 100))
        static = RATES.get(code)
        if static and static["kind"] == "peg":
            return eur_value_minor(code, amount_minor)   # peg: stated
        _refuse(f"no live rate for {code} and no statutory peg — "
                "unpriced exposure is stated, never zeroed")
