"""brandpack.py — the brand registry: per-country, per-operator skins.

The standing doctrine holds for pixels too: a country, a ministry, an
operator is a registry entry, never a code branch. Re-skinning the
platform for Cabo Verde's INPS or Nigeria's NHIA is ratifying an
entry, not forking a theme.

Doctrine:
- A BRAND IS A COMPLETE ENTRY: palette tokens, wordmark, logo ref,
  hero ref, text direction. Unknown tokens refuse — absence is
  stated, not defaulted.
- BRANDS ARE FOUR-EYES: propose, ratify (never the same hand),
  retire with a stated reason. Only live brands emit CSS.
- THE CSS IS A BUILD ARTIFACT: emit_css() generates the override
  sheet; nobody hand-edits a brand's variables in the repo.
"""

import hashlib
import json
import re

VERSION = "brandpack@1.0.0"

# The theme tokens a brand may set — the same custom properties the
# depth-theme sheet consumes. A token outside this set refuses: a
# brand cannot invent styling surface the pages do not read.
THEME_TOKENS = (
    "bg", "ink", "header", "card", "hairline",
    "accent", "accent-ink", "ok", "ok-bg",
    "danger", "danger-bg", "warn", "warn-bg",
    "banner-bg", "banner-border", "banner-ink",
)

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class Error(Exception):
    pass


def _sha(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


# Reference entries — ratified in seed_reference(). Palettes stay
# within the same dark demo family; operators re-skin within doctrine.
REFERENCE_BRANDS = {
    "ng": {
        "wordmark": "OpenHealth · Nigeria",
        "operator": "NHIA",
        "logo_ref": "icons/logo-ng.svg",
        "hero_ref": "icons/hero-ng.svg",
        "direction": "ltr",
        "palette": {
            "header": "#12324a", "accent": "#9ec7ff",
            "ok": "#7be3a3", "banner-bg": "#3d2e00",
        },
    },
    "cv": {
        "wordmark": "OpenHealth · Cabo Verde",
        "operator": "INPS-CV",
        "logo_ref": "icons/logo-cv.svg",
        "hero_ref": "icons/hero-cv.svg",
        "direction": "ltr",
        "palette": {
            "header": "#0f3d33", "accent": "#ffd77a",
            "ok": "#7be3a3", "banner-bg": "#12324a",
        },
    },
}


class BrandRegistry:
    """Four-eyes brand entries. The hand that proposes never approves."""

    def __init__(self):
        self.brands = {}          # brand_id -> entry
        self.history = []         # every transition, stated

    def _validate(self, definition):
        for key in ("wordmark", "operator", "logo_ref", "hero_ref",
                    "direction", "palette"):
            if key not in definition:
                raise Error(f"brand entry missing {key!r} — a brand "
                            "is a complete entry, absence is stated")
        if definition["direction"] not in ("ltr", "rtl"):
            raise Error("direction is ltr or rtl — stated, "
                        "never assumed")
        palette = definition["palette"]
        if not isinstance(palette, dict) or not palette:
            raise Error("a brand sets at least one palette token")
        for token, value in palette.items():
            if token not in THEME_TOKENS:
                raise Error(f"unknown theme token {token!r} — the "
                            "pages cannot read what they do not read")
            if not _HEX.match(str(value)):
                raise Error(f"token {token!r} is not a hex colour — "
                            "a palette is exact, not vibes")

    def propose(self, brand_id, definition, *, by, day):
        if not brand_id:
            raise Error("a brand entry names its id")
        if brand_id in self.brands and \
                self.brands[brand_id]["status"] == "live":
            raise Error(f"brand {brand_id!r} is live — retire before "
                        "re-proposing")
        self._validate(definition)
        self.brands[brand_id] = {**definition, "status": "proposed",
                                 "proposed_by": by, "proposed_day": day}
        self.history.append({"event": "proposed", "brand": brand_id,
                             "by": by, "day": day})

    def ratify(self, brand_id, *, by, day):
        entry = self.brands.get(brand_id)
        if not entry or entry["status"] != "proposed":
            raise Error(f"no proposed brand {brand_id!r}")
        if by == entry["proposed_by"]:
            raise Error("the hand that proposes never approves")
        entry["status"] = "live"
        entry["ratified_by"] = by
        entry["ratified_day"] = day
        self.history.append({"event": "ratified", "brand": brand_id,
                             "by": by, "day": day})

    def retire(self, brand_id, *, by, day, reason):
        entry = self.brands.get(brand_id)
        if not entry or entry["status"] != "live":
            raise Error(f"no live brand {brand_id!r}")
        if not (reason and reason.strip()):
            raise Error("retirement states its reason")
        entry["status"] = "retired"
        self.history.append({"event": "retired", "brand": brand_id,
                             "by": by, "day": day,
                             "reason": reason.strip()})

    def live(self, brand_id=None):
        live = {k: v for k, v in self.brands.items()
                if v["status"] == "live"}
        if brand_id is None:
            return live
        if brand_id not in live:
            raise Error(f"no live brand {brand_id!r}")
        return live[brand_id]

    def audit(self):
        return {"brands": len(self.brands),
                "live": sum(1 for b in self.brands.values()
                            if b["status"] == "live"),
                "history": list(self.history),
                "sha256": _sha({"brands": self.brands,
                                "history": self.history}),
                "version": VERSION}


def emit_css(entry):
    """One brand entry -> a CSS override sheet. Deterministic: same
    entry, same bytes. Only live entries reach here."""
    lines = ["/* GENERATED from brandpack registry — do not hand-edit",
             f" * brand: {entry['wordmark']} · operator "
             f"{entry['operator']} · generator: {VERSION} */",
             ":root {"]
    for token in sorted(entry["palette"]):
        lines.append(f"  --oh-{token}: {entry['palette'][token]};")
    lines.append("}")
    return "\n".join(lines) + "\n"


def seed_reference():
    """The two reference brands, four-eyes ratified."""
    reg = BrandRegistry()
    for brand_id, definition in REFERENCE_BRANDS.items():
        reg.propose(brand_id, definition, by="design-lead", day=1)
        reg.ratify(brand_id, by="programme-director", day=1)
    return reg


def export_css(out_dir):
    """Write brand.{id}.css for every live reference brand."""
    import os
    reg = seed_reference()
    paths = []
    for brand_id in sorted(reg.live()):
        css = emit_css(reg.live(brand_id))
        path = os.path.join(out_dir, f"brand.{brand_id}.css")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(css)
        paths.append(path)
    return paths


if __name__ == "__main__":
    import os
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "apps", "pwa", "css")
    for p in export_css(out):
        print(f"wrote {p}")
