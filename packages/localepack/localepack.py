"""localepack.py — the country-context registry.

The platform's standing doctrine: every country, currency, telco, and
insurer is a registry entry, never a code branch. This module is the
single home for the country context — the locale pack — that every
seed, demo, and scenario builder reads. Switching the platform from
Nigeria to Cabo Verde is switching the registry entry, not forking code.

Doctrine:
- A LOCALE IS A COMPLETE CONTEXT: country, currency, languages,
  facilities, name pools, payer, formulary prefix, vaccine schedule,
  phone format, and demo-scenario parameters. Absence of any element
  is stated, not defaulted.
- LOCALES ARE ENTRIES: a new country is a new dict in the registry.
  Unknown locale codes refuse.
- SEEDING IS DETERMINISTIC: the same locale + seed produces the same
  synthetic population, byte-for-byte.
- THE SCENARIO NAMES ITS AUDIENCE: ministry-demo, campus-pilot, or
  clinical-drill — framing changes, numbers don't.
"""

import hashlib
import json
import random

VERSION = "localepack@1.0.0"

# ---- locale registry ---------------------------------------------------------
# Each entry is a complete country context. Adding a country = adding an entry.

LOCALES = {
    "ng": {
        "country": "Nigeria",
        "country_code": "NG",
        "currency": {"code": "NGN", "symbol": "₦", "minor_unit": 2,
                     "minor_name": "kobo"},
        "languages": ["en", "pcm", "ha", "yo", "ig"],
        "default_language": "en",
        "formulary_prefix": "NG-",
        "vaccine_schedule": "NPI",
        "payer": {"name": "NHIA", "scheme_type": "social-insurance"},
        "facilities": [
            {"id": "NG-LUTH", "name": "Lagos University Teaching Hospital",
             "tier": "tertiary", "state": "Lagos"},
            {"id": "NG-UCH", "name": "University College Hospital Ibadan",
             "tier": "tertiary", "state": "Oyo"},
            {"id": "NG-AKTH", "name": "Aminu Kano Teaching Hospital",
             "tier": "tertiary", "state": "Kano"},
            {"id": "NG-GH-ILE", "name": "General Hospital Ile-Ife",
             "tier": "secondary", "state": "Osun"},
            {"id": "NG-PHC-SUR", "name": "Surulere PHC",
             "tier": "primary", "state": "Lagos"},
        ],
        "name_pools": {
            "given": ["Adaeze", "Chidi", "Ngozi", "Olumide", "Fatima",
                      "Ibrahim", "Yetunde", "Emeka", "Aisha", "Tunde",
                      "Blessing", "Kunle", "Amara", "Seun", "Halima"],
            "family": ["Okafor", "Adebayo", "Eze", "Mohammed", "Adeleke",
                       "Okonkwo", "Balogun", "Ibrahim", "Nwosu",
                       "Adeyemi", "Bello", "Ogunleye", "Chukwu",
                       "Abubakar", "Fashola"],
        },
        "phone_prefix": "+234",
        "states": ["Lagos", "Oyo", "Kano", "Osun", "Kaduna", "Rivers",
                   "Enugu", "Borno", "Anambra", "Delta"],
        "demo_scenarios": {
            "campus-pilot": {"anchor_facility": "NG-LUTH",
                             "cohort_name": "unilag-sim",
                             "cohort_size": 40,
                             "premium_minor": 250000,
                             "premium_currency": "NGN",
                             "audience_label": "University of Lagos"},
            "ministry-demo": {"audience_label": "Federal Ministry of Health",
                              "focus": "national-scale + surveillance"},
        },
    },
    "cv": {
        "country": "Cabo Verde",
        "country_code": "CV",
        "currency": {"code": "CVE", "symbol": "Esc", "minor_unit": 2,
                     "minor_name": "centavo",
                     "peg": {"to": "EUR", "rate": 110.265}},
        "languages": ["pt", "kea"],
        "default_language": "pt",
        "formulary_prefix": "CV-",
        "vaccine_schedule": "PNV-CV",
        "payer": {"name": "INPS-CV",
                  "scheme_type": "social-insurance"},
        "facilities": [
            {"id": "CV-HAN", "name": "Hospital Agostinho Neto",
             "tier": "tertiary", "state": "Praia, Santiago"},
            {"id": "CV-HBS", "name": "Hospital Baptista de Sousa",
             "tier": "secondary", "state": "Mindelo, São Vicente"},
            {"id": "CV-HSR", "name": "Hospital de São Francisco Xavier",
             "tier": "secondary", "state": "Fogo"},
            {"id": "CV-CS-ASS", "name": "Centro de Saúde de Assomada",
             "tier": "primary", "state": "Santiago"},
            {"id": "CV-CS-SAL", "name": "Centro de Saúde do Sal",
             "tier": "primary", "state": "Sal"},
        ],
        "name_pools": {
            "given": ["Aline", "Bruno", "Carla", "Djair", "Elisa",
                      "Fábio", "Gilda", "Hernâni", "Ivone", "José",
                      "Katia", "Luís", "Márcia", "Nélson", "Olívia"],
            "family": ["Tavares", "Semedo", "Correia", "Monteiro",
                       "Andrade", "Baptista", "Cardoso", "Delgado",
                       "Évora", "Fortes", "Gonçalves", "Lopes",
                       "Morais", "Pina", "Soares"],
        },
        "phone_prefix": "+238",
        "states": ["Santiago", "São Vicente", "Sal", "Fogo", "Boa Vista",
                   "Santo Antão", "Maio", "Brava", "São Nicolau"],
        "demo_scenarios": {
            "campus-pilot": {"anchor_facility": "CV-HAN",
                             "cohort_name": "unicv-sim",
                             "cohort_size": 30,
                             "premium_minor": 50000,
                             "premium_currency": "CVE",
                             "audience_label": "Universidade de Cabo Verde"},
            "ministry-demo": {"audience_label":
                              "Ministério da Saúde de Cabo Verde",
                              "focus": "VHC border health + insular referral"},
        },
    },
}


class Error(Exception):
    pass


def locales():
    return sorted(LOCALES.keys())


def get_locale(code):
    if code not in LOCALES:
        raise Error(f"unknown locale: {code} — the registry is the "
                    "contract; register the country, don't fork the code")
    return LOCALES[code]


def _rng(seed):
    return random.Random(f"{VERSION}:{seed}")


def seed_population(locale_code, *, n, seed=42):
    """Deterministic synthetic population for a locale."""
    loc = get_locale(locale_code)
    rng = _rng(f"{locale_code}:pop:{seed}:{n}")
    given = loc["name_pools"]["given"]
    family = loc["name_pools"]["family"]
    people = []
    for i in range(n):
        g = rng.choice(given)
        f = rng.choice(family)
        people.append({
            "ref": f"Pax-{loc['country_code']}-{i+1:04d}",
            "name": f"{g} {f}",
            "language": rng.choice(loc["languages"]),
            "state": rng.choice(loc["states"]),
            "phone": f"{loc['phone_prefix']}"
                     f"{rng.randint(100_000_000, 999_999_999)}",
        })
    return people


def seed_scenario(locale_code, scenario, *, seed=42):
    """A complete, reproducible demo scenario for a locale."""
    loc = get_locale(locale_code)
    if scenario not in loc["demo_scenarios"]:
        raise Error(
            f"scenario '{scenario}' not defined for locale "
            f"'{locale_code}' — scenarios are registry entries too")
    spec = dict(loc["demo_scenarios"][scenario])
    anchor = spec.get("anchor_facility")
    if anchor:
        spec["anchor"] = [f for f in loc["facilities"]
                          if f["id"] == anchor][0]
    if "cohort_size" in spec:
        spec["members"] = seed_population(
            locale_code, n=spec["cohort_size"], seed=seed)
    body = {"locale": locale_code, "country": loc["country"],
            "scenario": scenario, "seed": seed, "spec": spec,
            "version": VERSION}
    body["sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False)
        .encode()).hexdigest()
    return body


def context_summary(locale_code):
    """What every surface shows: country, currency, payer, language."""
    loc = get_locale(locale_code)
    return {
        "country": loc["country"],
        "currency": loc["currency"]["code"],
        "currency_symbol": loc["currency"]["symbol"],
        "languages": loc["languages"],
        "payer": loc["payer"]["name"],
        "facilities": len(loc["facilities"]),
        "formulary_prefix": loc["formulary_prefix"],
        "vaccine_schedule": loc["vaccine_schedule"],
        "version": VERSION,
    }
