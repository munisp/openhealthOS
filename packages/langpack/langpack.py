"""langpack.py — the unified language registry and string store.

Three mechanisms carried strings before this module existed — the
clinical PWA dictionary, the VHC content layer, and the depth pages
hardcoded English. This registry is the single home going forward:
every language is an entry, every surface declares its coverage, and
the coverage report is computed from the actual dictionaries, never
declared.

Doctrine:
- A LANGUAGE IS A COMPLETE ENTRY: code, endonym, direction (ltr/rtl),
  script, family. Direction is stated, never assumed — an RTL entry
  flips layout, not just glyphs.
- UNKNOWN LANGUAGES REFUSE. A platform that guesses a language is a
  platform that lies to a nurse.
- FALLBACK IS STATED: t() returns the requested language, or the
  fallback with used_fallback=True, or the key itself — never a
  silent blank, never a wrong language presented as right.
- COVERAGE IS COMPUTED: the report reads the dictionaries as they
  are; a half-translated surface shows 50%, not a marketing number.
"""

import hashlib
import json

VERSION = "langpack@1.0.0"


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# ---- language registry -------------------------------------------------------
# Widely spoken African languages and the platform's working languages.
# Adding a language = adding an entry. Direction is per-entry.

LANGUAGES = {
    # working languages
    "en": {"name": "English", "direction": "ltr", "script": "Latin",
           "family": "working"},
    "pt": {"name": "Português", "direction": "ltr", "script": "Latin",
           "family": "working"},
    "fr": {"name": "Français", "direction": "ltr", "script": "Latin",
           "family": "working"},
    "ar": {"name": "العربية", "direction": "rtl", "script": "Arabic",
           "family": "working"},
    # west africa
    "pcm": {"name": "Naija", "direction": "ltr", "script": "Latin",
            "family": "west-africa"},
    "ha": {"name": "Hausa", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "yo": {"name": "Yorùbá", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "ig": {"name": "Igbo", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "ff": {"name": "Fulfulde", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "tw": {"name": "Twi", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "ee": {"name": "Eʋegbe", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "wo": {"name": "Wolof", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    "bm": {"name": "Bamanankan", "direction": "ltr", "script": "Latin",
           "family": "west-africa"},
    # east africa
    "sw": {"name": "Kiswahili", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    "am": {"name": "አማርኛ", "direction": "ltr", "script": "Ethiopic",
           "family": "east-africa"},
    "om": {"name": "Afaan Oromoo", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    "so": {"name": "Soomaali", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    "rw": {"name": "Ikinyarwanda", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    "lg": {"name": "Luganda", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    "ti": {"name": "ትግርኛ", "direction": "ltr", "script": "Ethiopic",
           "family": "east-africa"},
    "ki": {"name": "Gĩkũyũ", "direction": "ltr", "script": "Latin",
           "family": "east-africa"},
    # southern africa
    "zu": {"name": "isiZulu", "direction": "ltr", "script": "Latin",
           "family": "southern-africa"},
    "xh": {"name": "isiXhosa", "direction": "ltr", "script": "Latin",
           "family": "southern-africa"},
    "st": {"name": "Sesotho", "direction": "ltr", "script": "Latin",
           "family": "southern-africa"},
    "sn": {"name": "chiShona", "direction": "ltr", "script": "Latin",
           "family": "southern-africa"},
    "af": {"name": "Afrikaans", "direction": "ltr", "script": "Latin",
           "family": "southern-africa"},
    # central africa / islands
    "ln": {"name": "Lingála", "direction": "ltr", "script": "Latin",
           "family": "central-africa"},
    "mg": {"name": "Malagasy", "direction": "ltr", "script": "Latin",
           "family": "islands"},
}


def get_language(code):
    entry = LANGUAGES.get(code)
    if entry is None:
        raise Error(f"unknown language {code!r} — languages are "
                    "entries; register the language first")
    return entry


def direction(code):
    """ltr or rtl — stated by the entry, never assumed."""
    return get_language(code)["direction"]


# ---- string surfaces ---------------------------------------------------------

FALLBACK_LANGUAGE = "en"


class StringSurface:
    """One surface's strings across languages. A surface is a page or
    component family (depth chrome, clinical PWA, VHC content)."""

    def __init__(self, surface_id, fallback=FALLBACK_LANGUAGE):
        get_language(fallback)  # the fallback itself must exist
        self.surface_id = surface_id
        self.fallback = fallback
        self.strings = {}  # lang -> {key: text}

    def add(self, lang, strings):
        get_language(lang)
        if not isinstance(strings, dict) or not strings:
            raise Error("a language contribution carries strings")
        for key, text in strings.items():
            if not key or not isinstance(text, str) or not text:
                raise Error(f"empty string for {key!r} — absence is "
                            "stated, never blank")
        self.strings.setdefault(lang, {}).update(strings)

    def t(self, key, lang):
        """Translate with a stated fallback chain: requested language,
        then the fallback language, then the key itself. The caller
        always knows which rung answered."""
        get_language(lang)
        entry = self.strings.get(lang, {})
        if key in entry:
            return {"text": entry[key], "language": lang,
                    "fell_back": False}
        base = self.strings.get(self.fallback, {})
        if key in base:
            return {"text": base[key], "language": self.fallback,
                    "fell_back": True}
        return {"text": key, "language": None, "fell_back": True}

    def all_keys(self):
        keys = set()
        for strings in self.strings.values():
            keys.update(strings)
        return keys

    def coverage(self):
        """Computed honesty: for each language present, how many of the
        surface's keys exist, and which are missing. No rounding, no
        marketing numbers."""
        keys = sorted(self.all_keys())
        report = {}
        for lang, strings in sorted(self.strings.items()):
            missing = [k for k in keys if k not in strings]
            report[lang] = {
                "keys": len(strings),
                "of": len(keys),
                "missing": missing,          # stated, never hidden
            }
        return report

    def emit_json(self):
        """The build artifact the PWA consumes: strings plus direction
        metadata, so the client can flip layout, not just text."""
        return {
            "surface": self.surface_id,
            "fallback": self.fallback,
            "languages": {lang: LANGUAGES[lang]
                          for lang in sorted(self.strings)},
            "strings": {lang: self.strings[lang]
                        for lang in sorted(self.strings)},
            "version": VERSION,
        }


# ---- the depth-page chrome surface -------------------------------------------
# The common chrome of the depth demo pages. Page-specific labels fall
# back to English and say so.

DEPTH_CHROME = {
    "country_context": {
        "en": "Country context", "pt": "Contexto do país",
        "fr": "Contexte du pays", "pcm": "Country wey you dey",
        "ha": "Yanayin ƙasa", "yo": "Àyíká orílẹ̀-èdè",
        "ig": "Ọnọdụ obodo", "sw": "Muktadha wa nchi",
        "ar": "سياق البلد",
    },
    "facility": {
        "en": "Facility", "pt": "Unidade de saúde",
        "fr": "Établissement", "pcm": "Hospital",
        "ha": "Asibiti", "yo": "Ilé ìwòsàn",
        "ig": "Ụlọ ọgwụ", "sw": "Kituo cha afya",
        "ar": "المرفق الصحي",
    },
    "language": {
        "en": "Language", "pt": "Língua", "fr": "Langue",
        "pcm": "Language wey you want", "ha": "Harshe",
        "yo": "Èdè", "ig": "Asụsụ", "sw": "Lugha",
        "ar": "اللغة",
    },
    "demo_banner": {
        "en": "Synthetic demo data — deterministic seed. Every refusal "
              "below is a gate working.",
        "pt": "Dados de demonstração sintéticos — semente determinista. "
              "Cada recusa abaixo é um controlo a funcionar.",
        "fr": "Données de démonstration synthétiques — graine "
              "déterministe. Chaque refus ci-dessous est un contrôle "
              "qui fonctionne.",
        "pcm": "Demo data — di seed na di same everytime. Any refusal "
               "wey you see na gate wey dey work.",
        "ha": "Bayanan gwaji — tsaba iri ɗaya. Kowane ƙin a ƙasa ƙofar "
              "karewa ce tana aiki.",
        "yo": "Dàtà àpẹẹrẹ — irúgbìn kan ṣoṣo. Gbogbo ìkọ̀sẹ̀dáyò "
              "lábẹ̀ yìí ni ẹ̀nù-ònà tó Ń ṣiṣẹ́.",
        "ig": "Data ngosi — mkpụrụ otu. Mgbaghara ọ bụla dị n'okpuru "
              "bụ ọnụ ụzọ na-arụ ọrụ.",
        "sw": "Data ya onyesho — mbegu moja. Kila kukatazwa hapa chini "
              "ni lango linalofanya kazi.",
        "ar": "بيانات تجريبية تركيبية — بذرة ثابتة. كل رفض أدناه هو "
              "بوابة تعمل.",
    },
    "integrity": {
        "en": "Integrity", "pt": "Integridade", "fr": "Intégrité",
        "pcm": "Integrity", "ha": "Aminci", "yo": "Ìdájọ́ tòótọ́",
        "ig": "Nkwenye", "sw": "Uadilifu", "ar": "النزاهة",
    },
    "payload_sha256": {
        "en": "Payload sha256", "pt": "sha256 do pacote",
        "fr": "sha256 de la charge", "pcm": "Payload sha256",
        "ha": "sha256 na bayanai", "yo": "sha256 àkójọpọ̀",
        "ig": "sha256 nke data", "sw": "sha256 ya data",
        "ar": "بصمة sha256 للبيانات",
    },
    "versions": {
        "en": "Versions", "pt": "Versões", "fr": "Versions",
        "pcm": "Versions", "ha": "Sigogi", "yo": "Àwọn ẹ̀yà",
        "ig": "Ụdịdị", "sw": "Matoleo", "ar": "الإصدارات",
    },
}


def build_depth_surface():
    """The depth-chrome surface: one dict per language, keys from the
    canonical DEPTH_CHROME table. Unknown languages refuse at add()."""
    surface = StringSurface("depth-chrome")
    langs = set()
    for key, strings in DEPTH_CHROME.items():
        unknown = set(strings) - set(LANGUAGES)
        if unknown:
            raise Error(f"DEPTH_CHROME[{key!r}] carries unregistered "
                        f"languages: {sorted(unknown)}")
        langs |= set(strings)
    for lang in sorted(langs):
        surface.add(lang, {key: strings[lang]
                           for key, strings in DEPTH_CHROME.items()
                           if lang in strings})
    return surface


def audit(surfaces):
    return {"surfaces": {s.surface_id: s.coverage() for s in surfaces},
            "languages": len(LANGUAGES),
            "rtl": sorted(c for c, e in LANGUAGES.items()
                          if e["direction"] == "rtl"),
            "sha256": _sha({s.surface_id: s.coverage()
                            for s in surfaces}),
            "version": VERSION}


if __name__ == "__main__":
    import os
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "apps", "pwa", "js")
    surface = build_depth_surface()
    path = os.path.join(out, "depth-strings.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(surface.emit_json(), fh, indent=2, ensure_ascii=False)
    print(f"wrote {path}")
