"""Treasury — multi-currency settlement totals, peg-aware revaluation,
FX exposure per corridor. Finance sees the exposure before it is a
surprise.

Doctrine: mixed-currency sums refuse (settlement doctrine). Revaluation
reads rates from versioned peg entries, never hardcoded. A corridor
without a stated rate is reported as unpriced exposure, not zero.
"""

from vhmarkets import currency as _currency

TREASURY_VERSION = "vhtreasury@1.0.0"

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
