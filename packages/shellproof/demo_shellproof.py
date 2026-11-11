"""shellproof demo — deterministic fixture drills + a live audit of the
real tree, exported as a stamped payload (ADR-0082).

The drills are synthetic fixtures; the audit reads the REAL service
workers and the REAL proofindex registry at export time (statuses and
counts only, byte-stable while the tree is honest — if the estate
drifts, the freshness gate names this payload stale, which is the
point)."""

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import shellproof  # noqa: E402
import proofindex  # noqa: E402

DEMO_VERSION = "shellproof-demo@1.0.0"
CLOCK = "2026-11-11T20:30:00Z"
OUT = os.path.normpath(os.path.join(
    _HERE, "..", "..", "apps", "pwa", "shellproof.json"))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))

# What the wave-21 recon census found — static historical data.
PRE_WAVE = {
    "install_killers": [
        {"path": "apps/pwa/design-tokens.css",
         "detail": ("sw-vhc.js cached \"./design-tokens.css\" — the file "
                    "lives one level up at apps/design-tokens.css; "
                    "cache.addAll rejects on any 404, so the ENTIRE "
                    "vhc shell (59 URLs) never installed. Every board "
                    "page's offline guarantee was silently absent.")},
    ],
    "uncached": [
        "apps/pwa/appmarket.json", "apps/pwa/charting.json",
        "apps/pwa/emergency.html",
        "apps/pwa/inpatient.cv.json", "apps/pwa/inpatient.ng.json",
        "apps/pwa/radiology.html",
        "apps/pwa/specialty.cv.json", "apps/pwa/specialty.ng.json",
        "apps/pwa/vhc-br10.json", "apps/pwa/vhc-fund.json",
        "apps/pwa/vhc-growth.json", "apps/pwa/vhc-helpdesk.json",
        "apps/pwa/viewer.html",
    ],
    "unregistered_pages": [
        {"page": "apps/pwa/emergency.html",
         "detail": ("the break-glass emergency-access page registered NO "
                    "service worker — the one page that must work "
                    "offline above all others")},
        {"page": "apps/pwa/radiology.html",
         "detail": "reading worklist — no service worker"},
        {"page": "apps/pwa/viewer.html",
         "detail": "diagnostic viewer — no service worker"},
    ],
    "resolution": ("path fixed to ../design-tokens.css; 10 payloads added "
                   "to the vhc shell; 3 pages added to the app shell and "
                   "gained sw.js registration; probe.html is a stated "
                   "exclusion (diagnostic probe, run online by hand)"),
}

DOCTRINE = [
    ("cache.addAll rejects wholesale: ONE bad path uninstalls the entire "
     "offline guarantee — a manifest entry is a promise, and promises "
     "are gated."),
    ("A board cached without its payload renders 'loading…' forever "
     "offline — coverage is per-target, not per-page."),
    ("Exclusions are stated data with reasons; silence is not an "
     "exclusion."),
    ("The audit reads the real shells at export: if the estate drifts, "
     "this payload goes stale and the freshness gate names it."),
]

_FIXTURE_SW_A = ('const SHELL = ["./index.html", "./app.js", '
                 '"../tokens.css"];\n')
_FIXTURE_SW_B = 'const SHELL = ["./board.html", "./board.json"];\n'


def _fixture_audit(files, payloads, exclusions=None, drop=None):
    fs = set(files) | {"apps/pwa/sw-a.js", "apps/pwa/sw-b.js"}
    fs -= set(drop or ())
    sources = {"apps/pwa/sw-a.js": _FIXTURE_SW_A,
               "apps/pwa/sw-b.js": _FIXTURE_SW_B}
    return shellproof.audit(
        ".", shell_files=tuple(sources), payloads=payloads,
        exists=lambda p: p in fs,
        listdir=lambda: sorted(f for f in fs if f.endswith(".html")),
        read=lambda p: sources[p],
        exclusions=exclusions or {})


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def run_demo():
    payloads = [p["path"] for p in proofindex.PAYLOADS
                if p["path"].startswith("apps/pwa/")]
    live = shellproof.audit(REPO_ROOT, payloads=payloads)

    # Fixture drills — every fate named.
    base_files = {"apps/pwa/index.html", "apps/pwa/app.js",
                  "apps/tokens.css", "apps/pwa/board.html",
                  "apps/pwa/board.json"}
    control = _fixture_audit(base_files, ["apps/pwa/board.json"])
    killer = _fixture_audit(base_files, ["apps/pwa/board.json"],
                            drop={"apps/tokens.css"})
    gap = _fixture_audit(base_files | {"apps/pwa/ghost.json"},
                         ["apps/pwa/board.json", "apps/pwa/ghost.json"])
    excl = _fixture_audit(base_files | {"apps/pwa/probe.html"},
                          ["apps/pwa/board.json"],
                          exclusions={"apps/pwa/probe.html":
                                      "diagnostic page"})

    assert control["summary"]["all_installable"]
    assert control["summary"]["full_coverage"]
    assert killer["install_killers"] == ["apps/tokens.css"]
    assert gap["coverage"]["uncached"] == ["apps/pwa/ghost.json"]
    assert excl["coverage"]["excluded"][0]["path"] == \
        "apps/pwa/probe.html"

    drills = [
        {"drill": "all cached, all resolvable — the honest steady state",
         "verdict": "green", "severity": "green"},
        {"drill": ("one shell entry 404s — addAll rejects, the WHOLE "
                   "shell never installs; killer named"),
         "verdict": "install-killer: apps/tokens.css",
         "severity": "red"},
        {"drill": ("a registered payload in no shell — invisible "
                   "offline; gap named"),
         "verdict": "uncached: apps/pwa/ghost.json",
         "severity": "red"},
        {"drill": ("diagnostic page deliberately uncached — exclusion "
                   "is stated data with a reason"),
         "verdict": "excluded: apps/pwa/probe.html",
         "severity": "amber"},
    ]

    body = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK,
        "gate": {
            "library": "packages/shellproof/shellproof.py "
                       + shellproof.VERSION,
            "seam": ("audit(root, payloads=..., exists/listdir/read "
                     "INJECTED) — the gate test passes the live "
                     "registry; demos and unit tests pass fixtures"),
            "shells": list(shellproof.SHELL_FILES),
        },
        "pre_wave_findings": PRE_WAVE,
        "live_audit": {
            "summary": live["summary"],
            "install_killers": live["install_killers"],
            "uncached": live["coverage"]["uncached"],
            "excluded": live["coverage"]["excluded"],
        },
        "drills": drills,
        "doctrine": DOCTRINE,
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    payloadio.write_text(OUT, json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print("shellproof: %d drills, %d shell entries, sha %s -> %s"
          % (len(body["drills"]),
             body["live_audit"]["summary"]["shell_entries"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
