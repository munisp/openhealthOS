"""shipproof-demo@1.0.0 — the production-readiness evidence surface.

Answers "is this production ready?" the only honest way this platform
knows: per capability, with evidence, with named remainders and named
owners. The board's drills attack the gate itself — a claim with no
evidence must come back unsubstantiated (red), and a remainder without
an owner must be refused at the door.

Same contract as every demo exporter: fixed clock, registry order,
byte-identical reruns, compact-contract sha256 stamp. No tempdir
paths, no wall time, nothing read but the register.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shipgate import (CAPABILITIES, SHIPGATE_VERSION, ShipgateError,
                      assess, validate_entry, verdict_of)

DEMO_VERSION = "shipproof-demo@1.0.0"
CLOCK = lambda: "2026-11-10T11:00:00Z"

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "shipproof.json")


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**payload, "sha256": digest}


def _drills() -> list:
    """The gate attacked with marketing. Both attacks must be named."""
    drills = []

    # 1. A claim with no evidence — the adjective attack.
    marketing = {"id": "drill-claim", "title": "World-class platform",
                 "layer": "brochure", "evidence": [],
                 "outstanding": []}
    v = verdict_of(marketing)
    drills.append({"attack": "claim with no evidence",
                   "expected": "unsubstantiated", "verdict": v,
                   "severity": "red" if v == "unsubstantiated"
                               else "green",
                   "note": "'world-class' with an empty evidence list "
                           "is unsubstantiated — the gate cannot be "
                           "greened by adjectives"})

    # 2. A remainder with no owner — the "we'll sort it out" attack.
    orphan = {"id": "drill-orphan", "title": "Thing", "layer": "x",
              "evidence": [{"kind": "test-suite", "ref": "tests"}],
              "outstanding": [{"item": "certification",
                               "needs": "a real device",
                               "owner": "nobody"}]}
    try:
        validate_entry(orphan)
        verdict, note = "accepted", "owner 'nobody' was ACCEPTED — " \
                                     "the gate is broken"
    except ShipgateError as exc:
        verdict, note = "refused", str(exc)
    drills.append({"attack": "remainder with no owner",
                   "expected": "refused", "verdict": verdict,
                   "severity": "red" if verdict == "refused"
                               else "green",
                   "note": note})

    return drills


def run_demo():
    report = assess()
    drills = _drills()
    payload = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK(),
        "assessment": report,
        "drills": drills,
        "flags": {
            "all_substantiated":
                report["summary"]["all_substantiated"],
            "all_remainders_owned":
                report["summary"]["all_remainders_owned"],
            "drills_named": all(d["verdict"] == d["expected"]
                                for d in drills),
        },
        "honesty": {
            "why_demo": "the DATA on every evidence board is "
                        "synthetic and deterministic — real patient "
                        "data must never appear on a "
                        "screenshot-verifiable page; the machinery "
                        "being exercised is the production code",
            "no_blanket_verdict": "the strongest word this gate has "
                                  "is 'proven' — there is no "
                                  "'production ready' verdict, because "
                                  "readiness is a property of "
                                  "evidence, not of ambition",
        },
        "versions": {"shipgate": SHIPGATE_VERSION,
                     "demo": DEMO_VERSION},
    }
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")


if __name__ == "__main__":
    main()
