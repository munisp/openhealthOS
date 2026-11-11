"""adrproof demo — the ADR-chain gate: fixture drills + a live audit of
the real decision chain, exported as a stamped payload (ADR-0086).

The drills are synthetic fixtures; the audit reads the REAL
docs/architecture chain at export time (summary only, byte-stable
while the tree is honest — if the estate drifts, the freshness gate
names this payload stale, which is the point)."""

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import adrchain  # noqa: E402

DEMO_VERSION = "adrproof-demo@1.0.0"
CLOCK = "2026-11-11T21:00:00Z"
OUT = os.path.normpath(os.path.join(
    _HERE, "..", "..", "apps", "pwa", "adrproof.json"))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))

# What the wave-22 recon census found — static historical data.
PRE_WAVE = {
    "collisions": [
        {"number": "0060",
         "claimants": ["adr-0060-registry-lakehouse-feed.md (2026-10-20)",
                       "adr-0060-cds-hooks-and-governed-agent-tools.md (2026-11-09)"]},
        {"number": "0061",
         "claimants": ["adr-0061-third-party-integration-surface.md (2026-10-20)",
                       "adr-0061-agent-write-protocol-and-honest-counting.md (2026-11-09)"]},
        {"number": "0062",
         "claimants": ["adr-0062-apisix-third-party-gateway.md (2026-10-20)",
                       "adr-0062-agent-write-stack-integration.md (2026-11-10)"]},
    ],
    "em_dash_headlines": ["adr-0068", "adr-0072", "adr-0073", "adr-0074"],
    "status_decoration_variants": 14,
    "resolution": ("October decisions renumbered to ADR-0083/0084/0085 "
                   "(the November agent-write reference graph is larger); "
                   "19 inbound reference sites updated in the same wave; "
                   "4 em-dash headlines normalised to the canonical "
                   "'# ADR-NNNN:' form; status decorations accepted as "
                   "data, status ABSENCE gated amber"),
}

DOCTRINE = [
    ("A decision number is a contract: two files claiming one number "
     "makes every inbound reference ambiguous — red, all claimants "
     "named."),
    ("The filename and the headline must agree — a file that answers "
     "to two numbers is a mismatch, red."),
    ("A gap is not a defect — numbers can be retired — but a gap is "
     "STATED, amber; silence is the defect."),
    ("One canonical headline; a second variant is a defect. Normalise "
     "the files, never teach the gate a variant."),
    ("The audit reads the real chain at export: if the estate drifts, "
     "this payload goes stale and the freshness gate names it."),
]

_GOOD = "# ADR-{n:04d}: Decision {n}\n\nStatus: accepted (2026-01-01)\n"


def _fixture(named):
    def listdir(root, adr_dir):
        return sorted(named)
    def read(root, path):
        return named[path.rsplit("/", 1)[-1]]
    return adrchain.audit(".", listdir=listdir, read=read)


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def run_demo():
    live = adrchain.audit(REPO_ROOT)

    control = _fixture({f"adr-{n:04d}-decision-{n}.md": _GOOD.format(n=n)
                        for n in (1, 2, 3)})
    collision = _fixture({
        "adr-0060-registry-lakehouse-feed.md": _GOOD.format(n=60),
        "adr-0060-cds-hooks.md": _GOOD.format(n=60),
        "adr-0061-third-party.md": _GOOD.format(n=61),
    })
    gap = _fixture({
        "adr-0001-one.md": _GOOD.format(n=1),
        "adr-0003-three.md": _GOOD.format(n=3),
    })
    mismatch = _fixture({
        "adr-0007-renamed.md": ("# ADR-0009: headline never updated\n\n"
                                "Status: accepted\n"),
    })
    legacy = _fixture({
        "adr-0008-em-dash.md": ("# ADR-0008 — the retired variant\n\n"
                                "Status: accepted\n"),
    })

    assert control["summary"]["chain_ok"]
    assert not collision["summary"]["chain_ok"]
    assert collision["duplicates"][0]["number"] == 60
    assert gap["gaps"] == [2] and gap["summary"]["chain_ok"]
    assert not mismatch["summary"]["chain_ok"]
    assert not legacy["summary"]["chain_ok"]
    assert legacy["files"][0]["verdicts"][0]["kind"] == "no-title"

    drills = [
        {"drill": "a clean contiguous chain — the honest steady state",
         "verdict": "chain_ok", "severity": "green"},
        {"drill": ("this wave's collision replayed: two files claim "
                   "0060 — every claimant named"),
         "verdict": "duplicate-number: 0060 ×2 claimants",
         "severity": "red"},
        {"drill": ("a number retired — the hole is stated, not silent"),
         "verdict": "gap: 0002 stated", "severity": "amber"},
        {"drill": ("file renamed, headline never updated — filename "
                   "says 0007, headline says 0009"),
         "verdict": "number-mismatch: 0007≠0009", "severity": "red"},
        {"drill": ("the em-dash variant — normalised estate-wide this "
                   "wave; the gate refuses it"),
         "verdict": "no-title: '# ADR-0008 —' is not the canonical form",
         "severity": "red"},
    ]

    body = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK,
        "gate": {
            "library": "packages/adrchain/adrchain.py " + adrchain.VERSION,
            "seam": ("audit(root, listdir/read INJECTED) — fixtures and "
                     "the real tree run the exact same code path"),
            "adr_dir": adrchain.ADR_DIR,
        },
        "pre_wave_findings": PRE_WAVE,
        "live_audit": {"summary": live["summary"]},
        "drills": drills,
        "doctrine": DOCTRINE,
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(body, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print("adrproof: %d drills, %d ADR files, sha %s -> %s"
          % (len(body["drills"]),
             body["live_audit"]["summary"]["files"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
