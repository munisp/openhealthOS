"""Markets registry — the country layer of the country-agnostic doctrine.

A market is an ENTRY, never a branch. Adding a country means adding an
entry here (currency, data-protection authority, languages, telcos) and
a versioned pricing profile in vhcover.VHC_PROFILES — no code path may
say `if country ==`. Unknown market or currency: refuse, because
absence is not a value.
"""

MARKETS_VERSION = "vhmarkets@1.0.0"


class MarketsError(Exception):
    pass


def _refuse(msg):
    raise MarketsError(msg)


# ISO 4217 minor exponents honoured: XOF/XAF/RWF are zero-decimal in
# settlement practice (scale 1), the rest scale 100. `minor` names the
# minor unit for receipts — denominators visible, always.
CURRENCIES = {
    "NGN": {"symbol": "₦", "minor": "kobo",     "scale": 100, "locale": "en-NG"},
    "EUR": {"symbol": "€", "minor": "cents",    "scale": 100, "locale": "en-IE"},
    "CVE": {"symbol": "$", "minor": "centavos", "scale": 100, "locale": "pt-CV"},
    "USD": {"symbol": "$", "minor": "cents",    "scale": 100, "locale": "en-US"},
    "GBP": {"symbol": "£", "minor": "pence",    "scale": 100, "locale": "en-GB"},
    "GHS": {"symbol": "₵", "minor": "pesewas",  "scale": 100, "locale": "en-GH"},
    "KES": {"symbol": "KSh", "minor": "cents",  "scale": 100, "locale": "en-KE"},
    "ZAR": {"symbol": "R", "minor": "cents",    "scale": 100, "locale": "en-ZA"},
    "XOF": {"symbol": "CFA", "minor": "francs", "scale": 1,   "locale": "fr-SN"},
    "XAF": {"symbol": "FCFA", "minor": "francs", "scale": 1,  "locale": "fr-CM"},
    "RWF": {"symbol": "RF", "minor": "francs",  "scale": 1,   "locale": "rw-RW"},
    "TZS": {"symbol": "TSh", "minor": "cents",  "scale": 100, "locale": "sw-TZ"},
    "EGP": {"symbol": "E£", "minor": "piastres", "scale": 100, "locale": "ar-EG"},
    "MAD": {"symbol": "DH", "minor": "centimes", "scale": 100, "locale": "fr-MA"},
    "MZN": {"symbol": "MT", "minor": "centavos", "scale": 100, "locale": "pt-MZ"},
    "AOA": {"symbol": "Kz", "minor": "cêntimos", "scale": 100, "locale": "pt-AO"},
    "ETB": {"symbol": "Br", "minor": "santim",  "scale": 100, "locale": "am-ET"},
}

# Market entries across the continent + the archipelago. `dpa` is the
# data-protection authority the privacy pack reports to; None is an
# honest gap, stated — never invented.
MARKETS = {
    "ng": {"currency": "NGN", "dpa": "NDPC",
           "languages": ("en", "pcm", "ha", "yo", "ig"),
           "telcos": ("mtn-ng", "airtel-ng", "glo-ng", "9mobile-ng")},
    "cv": {"currency": "CVE", "dpa": "CNPD",
           "languages": ("pt", "kea"),
           "telcos": ("unitel-cv", "alou-cv")},
    "gh": {"currency": "GHS", "dpa": "DPA-GH",
           "languages": ("en", "tw", "ga"),
           "telcos": ("mtn-gh", "telecel-gh", "airteltigo-gh")},
    "ke": {"currency": "KES", "dpa": "ODPC",
           "languages": ("en", "sw"),
           "telcos": ("safaricom-ke", "airtel-ke")},
    "za": {"currency": "ZAR", "dpa": "INFORREG-ZA",
           "languages": ("en", "zu", "xh", "af"),
           "telcos": ("vodacom-za", "mtn-za", "cellc-za")},
    "sn": {"currency": "XOF", "dpa": "CDP-SN",
           "languages": ("fr", "wo"),
           "telcos": ("orange-sn", "free-sn")},
    "ci": {"currency": "XOF", "dpa": "ARTCI",
           "languages": ("fr",),
           "telcos": ("orange-ci", "moov-ci", "mtn-ci")},
    "tz": {"currency": "TZS", "dpa": "PDPC-TZ",
           "languages": ("sw", "en"),
           "telcos": ("vodacom-tz", "airtel-tz", "tigo-tz")},
    "rw": {"currency": "RWF", "dpa": "NCSA-RW",
           "languages": ("rw", "en", "fr"),
           "telcos": ("mtn-rw", "airtel-rw")},
    "eg": {"currency": "EGP", "dpa": "EDPC-EG",
           "languages": ("ar", "en"),
           "telcos": ("vodafone-eg", "orange-eg", "etisalat-eg")},
    "ma": {"currency": "MAD", "dpa": "CNDP-MA",
           "languages": ("ar", "fr", "zgh"),
           "telcos": ("maroc-telecom", "orange-ma", "inwi-ma")},
    "et": {"currency": "ETB", "dpa": None,   # no dedicated DPA yet — stated
           "languages": ("am", "en", "om"),
           "telcos": ("ethio-telecom", "safaricom-et")},
    "cm": {"currency": "XAF", "dpa": "ANTIC-CM",
           "languages": ("fr", "en"),
           "telcos": ("mtn-cm", "orange-cm")},
    "mz": {"currency": "MZN", "dpa": None,   # law drafted, authority pending
           "languages": ("pt",),
           "telcos": ("vodacom-mz", "tmcel-mz")},
    "ao": {"currency": "AOA", "dpa": "APD-AO",
           "languages": ("pt",),
           "telcos": ("unitel-ao", "movicel-ao")},
}


def market(country):
    m = MARKETS.get(country)
    if m is None:
        _refuse(f"unknown market {country!r} — add the entry, never the branch")
    return m


def currency(code):
    c = CURRENCIES.get(code)
    if c is None:
        _refuse(f"unknown currency {code!r} — add the entry, never the branch")
    return c


def market_currency(country):
    return currency(market(country)["currency"])


def fmt_minor(code, minor):
    """Minor units rendered with the currency's own minor-unit name:
    550000 NGN -> '550,000 kobo'. Denominators visible, always."""
    c = currency(code)
    return f"{minor:,} {c['minor']}"


def fmt_major(code, minor):
    c = currency(code)
    scale = c["scale"]
    if scale == 1:
        return f"{c['symbol']}{minor:,}"
    whole, frac = divmod(minor, scale)
    return f"{c['symbol']}{whole:,}.{frac:02d}"


def report():
    return {"version": MARKETS_VERSION,
            "currencies": len(CURRENCIES), "markets": len(MARKETS),
            "markets_without_dpa": sorted(
                k for k, v in MARKETS.items() if v["dpa"] is None)}
