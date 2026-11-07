"""Actuarial data product — the marts become a sellable API.

Insurers and reinsurers buy k-floored, pseudonymized loss-ratio
extracts by corridor, season, and claim type. Every extract is a
catalog entry with a stated price, ships under a license token, and
enforces the k-floor before a single row leaves — small cells never
leak a person, whether the buyer is a ministry or a reinsurer.
"""

import hashlib
import json
import secrets

EXTRACT_VERSION = "vhextract@1.0.0"

K_FLOOR = 5                    # same floor as the surveillance packs

# The catalog — an extract is a priced entry, never a bespoke handshake
EXTRACT_CATALOG = {
    "corridor-loss-ratios@1.0.0": {
        "price_minor": 250_000, "currency": "EUR",
        "grain": ("corridor", "month"), "includes": ("loss_ratio",
                                                     "claim_count_kfloored",
                                                     "premium_minor")},
    "seasonality-pack@1.0.0": {
        "price_minor": 400_000, "currency": "EUR",
        "grain": ("claim_type", "season"),
        "includes": ("claim_count_kfloored", "avg_payout_minor")},
    "provider-scorecards@1.0.0": {
        "price_minor": 600_000, "currency": "EUR",
        "grain": ("provider_class", "quarter"),
        "includes": ("denial_rate", "settlement_ppm")},
}


class ExtractError(Exception):
    pass


def _refuse(msg):
    raise ExtractError(msg)


def k_floor(rows):
    """Suppress cells below the floor. Returns (kept, suppressed)."""
    kept, suppressed = [], 0
    for r in rows:
        if r.get("n", 0) < K_FLOOR:
            suppressed += 1
        else:
            kept.append(r)
    return kept, suppressed


class ExtractDesk:
    def __init__(self):
        self.licenses = {}        # token -> license record
        self.sales = []

    def buy(self, extract_id, *, buyer, day, payment_ref):
        e = EXTRACT_CATALOG.get(extract_id)
        if e is None:
            _refuse(f"unknown extract {extract_id!r} — the catalog is "
                    "the shelf")
        if not buyer or not payment_ref:
            _refuse("a sale names the buyer and the money")
        token = "ext_" + secrets.token_hex(12)
        lic = {"token_sha256": hashlib.sha256(token.encode()).hexdigest(),
               "extract": extract_id, "buyer": buyer, "day": day,
               "payment_ref": payment_ref}
        self.licenses[token] = lic
        self.sales.append({"extract": extract_id, "buyer": buyer,
                           "price_minor": e["price_minor"],
                           "currency": e["currency"], "day": day})
        return {"token": token, "extract": extract_id}

    def fulfill(self, token, *, rows):
        """k-floor enforced at the door, then the pack ships stamped."""
        lic = self.licenses.get(token)
        if lic is None:
            _refuse("no license — an extract without a sale is a leak")
        kept, suppressed = k_floor(rows)
        pack = {"version": EXTRACT_VERSION,
                "extract": lic["extract"], "buyer": lic["buyer"],
                "rows": kept, "suppressed_cells": suppressed,
                "k_floor": K_FLOOR}
        canon = json.dumps(pack, sort_keys=True).encode()
        pack["sha256"] = hashlib.sha256(canon).hexdigest()
        return pack

    def report(self):
        return {"version": EXTRACT_VERSION,
                "catalog": len(EXTRACT_CATALOG), "sales": len(self.sales),
                "gross_minor_by_currency": _gross(self.sales)}


def _gross(sales):
    out = {}
    for s in sales:
        out[s["currency"]] = out.get(s["currency"], 0) + s["price_minor"]
    return out
