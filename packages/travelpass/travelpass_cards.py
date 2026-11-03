"""travelpass_cards.py — international card payments and ICVP-shaped
vaccination cards for the travel pass. Two doctrines carry the file:
the PAN never lands, and a stamp without a batch is a drawing."""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from travelpass import TravelPassError  # noqa: E402


# ----------------------------------------------------------------
# International cards: the traveler pays with what they carry — Visa,
# Mastercard, Amex, UnionPay, JCB, RuPay, Discover, Diners — through a
# PSP that actually exists. Doctrines:
#   - THE PAN NEVER LANDS: we store scheme + last4 + a hash. A card
#     number at rest is a breach with a timestamp (PCI is not a
#     suggestion, it is the reason the vault is not ours).
#   - Luhn and length are plausibility gates, not fraud checks.
#   - Card-not-present without authentication is a chargeback with a
#     timestamp — refuse.
#   - A card form without a PSP is a phishing page: no charge
#     transport, no pretend.
CARD_SCHEMES_VERSION = "card-schemes@1.0.0"


def _luhn_ok(pan):
    total = 0
    for i, ch in enumerate(reversed(pan)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


CARD_SCHEMES = (
    # (scheme, prefix-matcher, allowed lengths) — order matters where
    # ranges overlap (81x co-badges UnionPay/RuPay); overlaps are
    # REPORTED as candidates, the PSP declaration settles them
    ("amex", lambda p: p[:2] in ("34", "37"), (15,)),
    ("diners", lambda p: p[:2] in ("36", "38") or p[:3] in
     ("300", "301", "302", "303", "304", "305"), (14,)),
    ("visa", lambda p: p[:1] == "4", (13, 16, 19)),
    ("mastercard", lambda p: 51 <= int(p[:2]) <= 55
     or 2221 <= int(p[:4]) <= 2720, (16,)),
    ("discover", lambda p: p[:4] == "6011" or p[:2] == "65"
     or 644 <= int(p[:3]) <= 649, (16, 19)),
    ("jcb", lambda p: 3528 <= int(p[:4]) <= 3589, (16, 17, 18, 19)),
    ("unionpay", lambda p: p[:2] == "62", (16, 17, 18, 19)),
    ("rupay", lambda p: p[:2] in ("60", "81", "82", "65")
     or p[:4] == "508", (16,)),
)

# PSPs the platform composes with — descriptors, not SDKs. The charge
# callable is supplied at deployment (Stripe/Paystack/Flutterwave
# server-side clients); nothing proprietary ships here.
PSPS = {
    "stripe": {"posture": "composed",
               "note": "PaymentIntents; SCA/3DS2 native — the European "
                       "cards demand it"},
    "paystack": {"posture": "composed",
                 "note": "Nigerian acquirer; NGN settlement, card + "
                         "transfer channels"},
    "flutterwave": {"posture": "composed",
                    "note": "pan-African acquiring incl. UnionPay "
                            "routing"},
    "adyen": {"posture": "composed",
              "note": "global acquiring; UnionPay/JCB/BCMC lanes for "
                      "Asian and European cards"},
}


def detect_scheme(pan):
    """Scheme candidates from the BIN. Luhn failure refuses outright —
    a number that fails its own checksum is a typo or a lie."""
    digits = "".join(ch for ch in pan if ch.isdigit())
    if not (13 <= len(digits) <= 19):
        raise TravelPassError(f"PAN length {len(digits)} outside "
                              "13–19 — not a card number")
    if not _luhn_ok(digits):
        raise TravelPassError("PAN fails Luhn — a number that fails "
                              "its own checksum is a typo or a lie; "
                              "re-enter, never retry blindly")
    candidates = [name for name, match, lengths in CARD_SCHEMES
                  if len(digits) in lengths and _safe_prefix(name,
                                                             digits)]
    if not candidates:
        raise TravelPassError("unrecognized card scheme — unbound is "
                              "not a code; the PSP cannot route what "
                              "we cannot name")
    return {"candidates": candidates, "scheme": candidates[0],
            "ambiguous": len(candidates) > 1,
            "version": CARD_SCHEMES_VERSION}


def _safe_prefix(name, digits):
    for n, matcher, lengths in CARD_SCHEMES:
        if n == name:
            try:
                return matcher(digits)
            except (ValueError, IndexError):
                return False
    return False


def pay_card(registry, traveler_id, *, pan, expiry, holder, psp_name,
             psp_charge, amount_kobo, reference, day,
             authenticated=False):
    """Charge an international card through a composed PSP. The ledger
    records scheme/last4/hash — never the PAN."""
    if psp_name not in PSPS:
        raise TravelPassError(f"unknown PSP: {psp_name} — the PSP menu "
                              "is versioned; an unlisted gateway is a "
                              "leak, not a lane")
    if psp_charge is None:
        raise TravelPassError(
            f"{psp_name}: no charge transport — a card form without a "
            "PSP is a phishing page; wire the PSP's server client at "
            "deployment")
    if not authenticated:
        raise TravelPassError(
            "card-not-present without authentication (3DS/SCA) is a "
            "chargeback with a timestamp — authenticate first")
    if not holder:
        raise TravelPassError("cardholder name required")
    det = detect_scheme(pan)
    digits = "".join(ch for ch in pan if ch.isdigit())
    result = psp_charge({"amount_kobo": amount_kobo,
                         "scheme": det["scheme"],
                         "last4": digits[-4:], "reference": reference})
    if not result or result.get("status") != "approved":
        raise TravelPassError(
            f"charge declined or unconfirmed — the payment did not "
            f"happen; the ledger records attempts as refusals, not "
            f"receipts ({(result or {}).get('status', 'no-response')})")
    entry = registry.pay(traveler_id, amount_kobo=amount_kobo,
                         method="card",
                         reference=reference, day=day)
    entry["card"] = {"scheme": det["scheme"], "last4": digits[-4:],
                     "pan_sha256": hashlib.sha256(
                         digits.encode()).hexdigest(),
                     "psp": psp_name,
                     "auth_code": result.get("auth_code"),
                     "scheme_ambiguity": det["candidates"]
                     if det["ambiguous"] else None}
    return entry


# ----------------------------------------------------------------
# Vaccination cards: the ICVP-shaped traveler record. A stamp without
# a batch number is a drawing, not a certificate; a vaccine inside its
# incubation window is not yet protection (yellow fever counts from
# day 10); entry requirements are versioned per destination because
# the list changes with the epidemiology, not the politics.
VACCINE_CARD_VERSION = "icvp-shaped@1.0.0"
VACCINES = {
    # vaccine -> (days until valid, validity days or None = lifetime)
    "yellow-fever": (10, None),   # WHO IHR 2016: lifetime
    "polio-ipv": (0, 365),        # traveler boosters, shaped
    "meningitis-acwy": (10, 1095),
    "covid-19": (14, 365),        # shaped — bind national rules
    "measles": (14, None),
}
ENTRY_REQUIREMENTS = {  # destination -> required vaccines, versioned
    "ng": ("yellow-fever",),
    "sa": ("yellow-fever", "meningitis-acwy"),  # Hajj-shaped
}


def record_vaccination(registry, traveler_id, *, vaccine, day,
                       certificate_ref, batch, administering_country):
    t = registry._t(traveler_id)
    if vaccine not in VACCINES:
        raise TravelPassError(f"unknown vaccine: {vaccine} — the menu "
                              "is versioned, not improvised")
    if not certificate_ref or not batch:
        raise TravelPassError("a stamp without a certificate reference "
                              "and a batch is a drawing, not a "
                              "certificate")
    if not administering_country:
        raise TravelPassError("the vaccinating country is part of the "
                              "provenance — a certificate from nowhere "
                              "certifies nothing")
    rec = {"vaccine": vaccine, "day": day,
           "certificate_ref": certificate_ref, "batch": batch,
           "country": administering_country,
           "version": VACCINE_CARD_VERSION}
    t.setdefault("vaccinations", []).append(rec)
    return dict(rec)


def vaccination_card(registry, traveler_id):
    """The traveler's card: every shot with its provenance, validity
    computed at read time against the versioned rules."""
    t = registry._t(traveler_id)
    out = []
    for v in t.get("vaccinations", []):
        onset, valid_days = VACCINES[v["vaccine"]]
        out.append({**v,
                    "valid_from_day": v["day"] + onset,
                    "valid_until_day": (None if valid_is_lifetime(v)
                                        else v["day"] + onset
                                        + VACCINES[v["vaccine"]][1])})
    return {"traveler_id": traveler_id, "vaccinations": out,
            "version": VACCINE_CARD_VERSION}


def valid_is_lifetime(v):
    return VACCINES[v["vaccine"]][1] is None


def entry_clearance(registry, traveler_id, *, destination, day):
    """Does this traveler meet the destination's versioned vaccine
    requirements TODAY? Missing, too-fresh, or expired vaccinations
    name the gap — clearance never papers over it."""
    if destination not in ENTRY_REQUIREMENTS:
        raise TravelPassError(f"no entry requirements on file for "
                              f"'{destination}' — unlisted destination "
                              "is a policy gap, not a free pass")
    t = registry._t(traveler_id)
    gaps = []
    for vaccine in ENTRY_REQUIREMENTS[destination]:
        recs = [v for v in t.get("vaccinations", [])
                if v["vaccine"] == vaccine]
        if not recs:
            gaps.append(f"{vaccine}: not on record")
            continue
        onset, valid_days = VACCINES[vaccine]
        valid = [v for v in recs if v["day"] + onset <= day and
                 (valid_days is None
                  or day <= v["day"] + onset + valid_days)]
        if not valid:
            fresh = [v for v in recs if day < v["day"] + onset]
            gaps.append(f"{vaccine}: "
                        + ("inside the onset window (valid from day "
                           f"{recs[-1]['day'] + onset})" if fresh
                           else "expired"))
    return {"traveler_id": traveler_id, "destination": destination,
            "cleared": not gaps, "gaps": gaps,
            "requirements_version": VACCINE_CARD_VERSION}


# ----------------------------------------------------------------
# Surveillance: the border is the country's first epidemiological
# sensor. A notifiable condition on a traveler record is an
# importation signal, and the PoE screen is where it's caught.
NOTIFIABLE = {  # condition keyword -> surveillance syndrome
    "yellow-fever": "jaundice",
    "measles": "measles_like_rash",
    "cholera": "acute_watery_diarrhoea",
    "mpox": "fever_with_rash",
    "covid": "acute_respiratory_infection",
    "lassa": "fever_with_rash",
}
NOTIFIABLE_VERSION = "traveler-notifiable@1.0.0"
