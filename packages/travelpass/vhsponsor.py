"""Diaspora-sponsored cover — the payer is not the traveller.

A sponsor (diaspora, employer, family) buys cover for a named
traveller, paying in their own currency while the cover prices and
settles in the destination profile's currency. The FX quote is a
versioned rate entry (vhmarkets/vhtreasury), the sponsorship is a
named relationship in the audit, and the traveller receives the pass
by link — a WhatsApp deep link is a transport detail, never a ledger.

Doctrine: sponsor ≠ traveller is stated on the cover; the money trail
and the care trail are both auditable.
"""

import hashlib

from vhmarkets import currency as _currency
from vhtreasury import RATES

SPONSOR_VERSION = "vhsponsor@1.0.0"


class SponsorError(Exception):
    pass


def _refuse(msg):
    raise SponsorError(msg)


def fx_quote(*, from_currency, to_currency, amount_minor, day):
    """Sponsor currency -> destination currency via the stated rates.
    Unknown rate: refuse — an unstated rate is not a quote."""
    _currency(from_currency)
    _currency(to_currency)
    if amount_minor <= 0:
        _refuse("a zero amount is not a quote")
    if from_currency == to_currency:
        return {"from": from_currency, "to": to_currency,
                "amount_minor": amount_minor, "rate": 1.0, "day": day}
    fr, tr = RATES.get(from_currency), RATES.get(to_currency)
    if fr is None or tr is None:
        _refuse("no stated rate for this pair — unpriced is stated, "
                "never guessed")
    eur = amount_minor / _currency(from_currency)["scale"] / fr["per_eur"]
    out_minor = int(round(eur * tr["per_eur"] *
                          _currency(to_currency)["scale"]))
    return {"from": from_currency, "to": to_currency,
            "amount_minor": out_minor,
            "rate": round(tr["per_eur"] / fr["per_eur"], 6), "day": day}


def whatsapp_deeplink(*, pass_url, text):
    """The delivery link for the pass. WhatsApp is a transport detail. """
    if not pass_url.startswith("https://"):
        _refuse("the pass link is TLS or it does not travel")
    from urllib.parse import quote
    return ("https://wa.me/?text=" +
            quote(f"{text} {pass_url}"))


class SponsorBook:
    def __init__(self):
        self.sponsorships = {}    # sponsor_ref -> record
        self.audit = []

    def sponsor_cover(self, vhc, *, sponsor_ref, sponsor_currency,
                      traveller_passport, traveller_name, days,
                      age_years, day, key_id):
        """Buy cover for someone else. Quote and purchase through the
        engine's own door; price in the destination profile, quote the
        sponsor in their own currency, name both parties."""
        if not sponsor_ref or not traveller_passport:
            _refuse("sponsorship names both parties — anonymous money is "
                    "not sponsorship")
        _currency(sponsor_currency)
        q = vhc.quote(days=days, age_years=age_years, day=day)
        cover = vhc.purchase(quote_id=q["quote_id"],
                             passport_ref=traveller_passport,
                             full_name=traveller_name,
                             entry_day=day, day=day, key_id=key_id)
        premium = q["total_minor"]
        profile = vhc.profile
        sponsor_quote = fx_quote(from_currency=sponsor_currency,
                                 to_currency=profile["currency"],
                                 amount_minor=premium, day=day)
        rec = {"sponsor_ref": sponsor_ref, "cover_ref": cover["ref"],
               "traveller": traveller_passport,
               "premium_minor": premium,
               "premium_currency": profile["currency"],
               "sponsor_quote": sponsor_quote, "day": day}
        rec["ref"] = "SPN-" + hashlib.sha256(
            f"{sponsor_ref}{cover['ref']}{day}".encode()).hexdigest()[:10]
        self.sponsorships[rec["ref"]] = rec
        self.audit.append({"event": "sponsor_cover", **rec})
        return dict(rec)

    def report(self):
        return {"version": SPONSOR_VERSION,
                "sponsorships": len(self.sponsorships)}
