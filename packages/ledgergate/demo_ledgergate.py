"""ledgerproof demo — the README-ledger gate: fixture drills + a live audit
of the real ledger, exported as a stamped payload (ADR-0087).

The drills are synthetic fixtures; the audit reads the REAL README.md
at export time (summary only, byte-stable while the tree is honest —
if the ledger drifts, the freshness gate names this payload stale,
which is the point)."""

import hashlib
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import ledgergate  # noqa: E402

DEMO_VERSION = "ledgerproof-demo@1.0.0"
CLOCK = "2026-11-11T22:00:00Z"
OUT = os.path.normpath(os.path.join(
    _HERE, "..", "..", "apps", "pwa", "ledgerproof.json"))
REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))

# What the wave-23 recon census found — static historical data.
PRE_WAVE = {
    "rows_found": 227,
    "duplicates": [
        {"number": 30,
         "claimants": ["line 44 — Provider economics rail (ADR-0032), in sequence",
                       "line 165 — Benefit visibility + tariff staleness (ADR-0031), displaced"]},
        {"number": 155,
         "claimants": ["line 379 — Helpdesk + 5-language content (vhsupport), engine row",
                       "line 385 — Pilot seed (vhpilot), demo row"]},
        {"number": 156,
         "claimants": ["line 380 — Actuarial data marts (vhmarts), engine row",
                       "line 386 — Staged go-live gates (vhgolive), demo row"]},
        {"number": 157,
         "claimants": ["line 381 — EASE single-rail adapter (vhease), engine row",
                       "line 387 — Ministry walkthrough (vhdemo), demo row"]},
        {"number": 158,
         "claimants": ["line 382 — Batch manifest pre-clearance (vhclearance), engine row",
                       "line 388 — One-command ministry demo (vhc-walkthrough.sh), demo row"]},
        {"number": 159,
         "claimants": ["line 383 — HSM key custody (vhcover KeyRing), engine row",
                       "line 389 — Audience-aware walkthroughs (vhdemo), demo row"]},
        {"number": 160,
         "claimants": ["line 384 — Proactive notifications (vhnotify), engine row",
                       "line 390 — Cabo Verde tailoring (vhdemo), demo row"]},
    ],
    "gap": 36,
    "displaced_block": {
        "rows": [85, 71, 30, 20, 19],
        "location": "after row 145 (lines 163-167), orphaned between sections",
    },
    "swaps": ["modules-table rows 10/11 transposed",
              "rows 37/38 inverted against their ADR numbers (ADR-0039 before ADR-0038)"],
    "suffix_anomaly": "94b existed with no 94a — the suffix sequence never started",
    "descending_tail": ("rows 221/220/219/218 ran newest-first against the "
                        "ascending body — an insertion habit, retired this wave"),
    "resolution": ("ADR-0031's displaced row renumbered 30→36 (filling the only gap); "
                   "demo block renumbered 155-160→222-227 and appended; displaced rows "
                   "19/20/71/85 returned to numeric position (71 restored ahead of its "
                   "own sub-row 71a); modules rows 10/11 and rows 37/38 put in order "
                   "(ADR-0038 is now row 37); 94b renamed 94a; the tail runs ascending "
                   "like the rest — 227 rows, 1..227, no duplicates, no gaps"),
}

DOCTRINE = [
    ("A row number is a contract: two rows claiming one number makes "
     "every citation of that row ambiguous — red, all claimants named."),
    ("The ledger is one ascending sequence — a reader verifies it by "
     "eye; an out-of-order row is a defect, red."),
    ("A gap is not a defect — a row can be retired — but a gap is "
     "STATED, amber; silence is the defect."),
    ("Sub-rows attach to their parent and start at 'a' — an orphan is "
     "red, a broken suffix sequence is stated amber."),
    ("The audit reads the real README at export: if the ledger drifts, "
     "this payload goes stale and the freshness gate names it."),
]

_ROW = "| {n} | fixture feature {n} | detail |"


def _fixture(rows):
    text = "# fixture\n\n| # | Feature | Detail |\n|---|---|---|\n" + "\n".join(rows) + "\n"
    return ledgergate.audit(".", read=lambda root, path: text)


def _stamp(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def run_demo():
    live = ledgergate.audit(REPO_ROOT)

    control = _fixture([_ROW.format(n=n) for n in (1, 2, 3)])
    collision = _fixture([
        _ROW.format(n=30).replace("fixture feature 30", "provider economics"),
        _ROW.format(n=30).replace("fixture feature 30", "benefit visibility"),
        _ROW.format(n=31),
    ])
    gap = _fixture([_ROW.format(n=n) for n in range(1, 36)] + [_ROW.format(n=37)])
    disorder = _fixture([_ROW.format(n=145), _ROW.format(n=85)])
    orphan = _fixture([_ROW.format(n=70), "| 71a | displaced sub-row | detail |", _ROW.format(n=72)])

    assert control["summary"]["ledger_ok"]
    assert not collision["summary"]["ledger_ok"]
    assert collision["summary"]["duplicates"] == [30]
    assert "benefit visibility" in collision["verdicts"][0]["detail"]
    assert gap["summary"]["gaps"] == [36] and gap["summary"]["ledger_ok"]
    assert not disorder["summary"]["ledger_ok"]
    assert disorder["summary"]["out_of_order"] == [85]
    assert not orphan["summary"]["ledger_ok"]
    assert orphan["summary"]["orphan_subrows"] == ["71a"]

    drills = [
        {"drill": "a clean ascending ledger — the honest steady state",
         "verdict": "ledger_ok", "severity": "green"},
        {"drill": ("this wave's collision replayed: two rows claim 30 — "
                   "every claimant named with line and excerpt"),
         "verdict": "duplicate: row 30 ×2 claimants", "severity": "red"},
        {"drill": ("a row retired — the hole at 36 is stated, not silent"),
         "verdict": "gap: 36 stated", "severity": "amber"},
        {"drill": ("the displaced block replayed: row 85 parked after "
                   "row 145 — the ledger is one ascending sequence"),
         "verdict": "out-of-order: row 85 follows row 145", "severity": "red"},
        {"drill": ("a sub-row separated from its parent — 71a without "
                   "71 directly above it"),
         "verdict": "orphan-subrow: 71a", "severity": "red"},
    ]

    body = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK,
        "gate": {
            "library": "packages/ledgergate/ledgergate.py " + ledgergate.VERSION,
            "seam": ("audit(root, read INJECTED) — fixtures and the real "
                     "tree run the exact same code path"),
            "readme": ledgergate.README,
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
    payloadio.write_text(OUT, json.dumps(body, indent=1, ensure_ascii=False) + "\n")
    print("ledgerproof: %d drills, %d rows, sha %s -> %s"
          % (len(body["drills"]),
             body["live_audit"]["summary"]["rows"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
