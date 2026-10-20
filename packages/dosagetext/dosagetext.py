"""OpenHealth OS — dosagetext: FHIR Dosage → patient-language instructions (ADR-0049).

Insight adopted clean-room from jy95/fds (Apache-2.0): a structured
FHIR Dosage should reach the patient as a readable instruction in THEIR
language — "Take 2 tablets twice daily", not a JSON blob. FDS is Java 17
+ ICU4J (we run neither) and its locales (en/fr/nl/de/es/it/pt) miss
every language this platform commits to, so this is a stdlib-Python
implementation of the same contract for en/pcm/ha/yo/ig.

Scope (the 80% of real prescriptions): doseQuantity + timing.repeat
(frequency/period/periodUnit), route, and as-needed flag. Complex
tapers/bounds fall back to a safe generic instruction rather than
guessing — a wrong instruction is worse than a plain one (same doctrine
as danger-sign detection biasing to false positives).

Used by: notification-service adherence nudges, pharmacy dispense
messages, the patient portal medication view.

CLI:
    python3 dosagetext.py render DOSAGE.json --lang pcm
"""
from __future__ import annotations

import argparse
import json
import sys

SUPPORTED = ("en", "pcm", "ha", "yo", "ig")

# --- phrase tables -------------------------------------------------------
# Each template keeps placeholders {dose} {unit} {freq} {route} {cond}.
# Translations are deliberately short and imperative — SMS-length,
# spoken-register (these ride the notification rail to feature phones).

_TAKE = {
    "en": "Take {dose} {unit}",
    "pcm": "Take {dose} {unit}",
    "ha": "Ka sha {dose} {unit}",
    "yo": "Mu {dose} {unit}",
    "ig": "Were {dose} {unit}",
}

_FREQ = {  # (frequency, period, periodUnit) -> per-language phrase
    (1, 1, "d"): {"en": "once daily", "pcm": "one time every day",
                  "ha": "sau daya a rana", "yo": "leekan ni ojoojumọ",
                  "ig": "otu ugboro kwa ụbọchị"},
    (2, 1, "d"): {"en": "twice daily", "pcm": "two times every day",
                  "ha": "sau biyu a rana", "yo": "lemeji ni ojoojumọ",
                  "ig": "ugboro abụọ kwa ụbọchị"},
    (3, 1, "d"): {"en": "three times daily", "pcm": "three times every day",
                  "ha": "sau uku a rana", "yo": "lemeta ni ojoojumọ",
                  "ig": "ugboro atọ kwa ụbọchị"},
    (4, 1, "d"): {"en": "four times daily", "pcm": "four times every day",
                  "ha": "sau hudu a rana", "yo": "lemerin ni ojoojumọ",
                  "ig": "ugboro anọ kwa ụbọchị"},
    (1, 1, "wk"): {"en": "once weekly", "pcm": "one time every week",
                   "ha": "sau daya a mako", "yo": "leekan ni oṣooṣe",
                   "ig": "otu ugboro kwa izu"},
}

_ROUTE = {
    "oral": {"en": "by mouth", "pcm": "for mouth",
             "ha": "ta baki", "yo": "nu enu", "ig": "site n'ọnụ"},
    "topical": {"en": "on the skin", "pcm": "for skin",
                "ha": "a fata", "yo": "lori awọ ara", "ig": "n'akpụkpọ ahụ"},
}

_PRN = {
    "en": "when needed", "pcm": "when you need am",
    "ha": "idan akwai bukata", "yo": "nigbati o ba nilo",
    "ig": "mgbe ị chọrọ ya",
}

_FALLBACK = {
    "en": "Take this medicine exactly as the prescriber explained.",
    "pcm": "Take this medicine exactly how the doctor tell you.",
    "ha": "Ka sha wannan magani kamar yadda likita ya fada.",
    "yo": "Mu oogun yi gege bi dokita se salaye.",
    "ig": "Were ọgwụ a otu dọkịta siri kwuo.",
}

_DEFAULT_UNIT = {
    "en": "tablet(s)", "pcm": "tablet", "ha": "kwamfuta",
    "yo": "tabuleeti", "ig": "mkpụrụ ọgwụ",
}


def render(dosage: dict, lang: str = "en") -> str:
    """FHIR R4 Dosage → instruction string. Unknown/underspecified shapes
    return the safe fallback (never a guess)."""
    if lang not in SUPPORTED:
        lang = "en"

    dose = dosage.get("doseAndRate") or [{}]
    dq = (dose[0].get("doseQuantity") or {})
    value, unit = dq.get("value"), dq.get("unit") or _DEFAULT_UNIT[lang]

    timing = (dosage.get("timing") or {}).get("repeat") or {}
    freq = timing.get("frequency")
    period = timing.get("period")
    punit = timing.get("periodUnit")

    if value is None or freq is None or period is None or punit is None:
        return _FALLBACK[lang]

    key = (freq, period, punit)
    freq_phrase = _FREQ.get(key)
    if freq_phrase is None:
        return _FALLBACK[lang]  # e.g. every-6-hours tapers: don't guess

    parts = [f"{_TAKE[lang].format(dose=value, unit=unit)} {freq_phrase[lang]}"]

    route = (dosage.get("route") or {}).get("text", "").lower()
    if route in _ROUTE:
        parts.append(_ROUTE[route][lang])

    if dosage.get("asNeededBoolean"):
        parts.append(_PRN[lang])

    return " ".join(parts) + "."


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="FHIR Dosage → human text")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render")
    r.add_argument("dosage")
    r.add_argument("--lang", default="en", choices=SUPPORTED)
    args = ap.parse_args(argv)
    print(render(json.load(open(args.dosage)), args.lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
