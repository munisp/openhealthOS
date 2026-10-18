#!/usr/bin/env python3
"""WCAG AA contrast gate for the app design systems (ADR-0013, improvement).

Sunlight-readable is a hard requirement, not a vibe: every text-on-surface
pair in the palettes must clear WCAG AA (4.5:1 normal text; 3:1 for faint
decorative hints). Parses CSS custom properties per scope (:root and each
[data-theme]) and checks the semantic pairs the apps actually use.

  python3 tools/a11y-contrast.py apps/pwa/css/app.css \
      apps/dashboards/css/dash.css apps/patient/css/app.css
"""
from __future__ import annotations

import re
import sys

FG_TEXT = ("--text", "--ink")
FG_MUTED = ("--muted",)
FG_FAINT = ("--faint",)
BG_MAIN = ("--bg", "--panel", "--panel-2", "--paper", "--surface")
SEMANTIC = (("--warn", "--warn-bg"), ("--danger", "--danger-bg"),
            ("--crit", "--danger-bg"), ("--info", "--info-bg"),
            ("--ok", "--ok-bg"), ("--green", "--ok-bg"))

SCOPE_RE = re.compile(r"(:root|\[data-theme=\"[^\"]+\"\])\s*\{([^}]*)\}", re.S)
VAR_RE = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b")


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def scopes(css: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for scope, body in SCOPE_RE.findall(css):
        out[scope] = dict(VAR_RE.findall(body))
    return out


def check_file(path: str) -> list[str]:
    failures: list[str] = []
    with open(path) as f:
        parsed = scopes(f.read())
    for scope, vars_ in parsed.items():
        def req(fg: str, bg: str, minimum: float) -> None:
            if fg in vars_ and bg in vars_:
                r = contrast(vars_[fg], vars_[bg])
                if r < minimum:
                    failures.append(f"{path} {scope}: {fg}({vars_[fg]}) on "
                                    f"{bg}({vars_[bg]}) = {r:.2f}:1 < {minimum}:1")
        for fg in FG_TEXT:
            for bg in BG_MAIN:
                req(fg, bg, 4.5)
        for fg in FG_MUTED:
            for bg in BG_MAIN:
                req(fg, bg, 4.5)
        for fg in FG_FAINT:
            for bg in BG_MAIN:
                req(fg, bg, 3.0)
        for fg, bg in SEMANTIC:
            req(fg, bg, 4.5)
    return failures


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: a11y-contrast.py CSS...", file=sys.stderr)
        return 2
    failures: list[str] = []
    for path in argv[1:]:
        failures.extend(check_file(path))
    if failures:
        print("WCAG AA contrast failures:")
        for f in failures:
            print("  " + f)
        return 1
    print(f"contrast gate: {len(argv) - 1} stylesheet(s) pass WCAG AA")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
