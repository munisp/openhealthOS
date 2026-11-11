"""shipproof-demo@1.1.0 — the production-readiness evidence surface.

Answers "is this production ready?" the only honest way this platform
knows: per capability, with evidence, with named remainders and named
owners — and, since wave 9, with every payload-named piece of evidence
re-checked against the live proof index before the gate speaks
(ADR-0070). The board's drills attack the gate itself: a claim with no
evidence must come back unsubstantiated (red), a remainder without an
owner must be refused at the door, and rotten or unregistered evidence
must void, by name.

Same contract as every demo exporter: fixed clock, registry order,
byte-identical reruns, compact-contract sha256 stamp. No tempdir
paths, no wall time. The cross-check reads the real tree; only
STABLE data (statuses, never presented hashes) enters the payload.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)
sys.path.insert(0, os.path.normpath(os.path.join(_here, "..",
                                                 "proofindex")))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import proofindex
from shipgate import (CAPABILITIES, SHIPGATE_VERSION, ShipgateError,
                      assess, validate_entry, verdict_of)

DEMO_VERSION = "shipproof-demo@1.1.0"
CLOCK = lambda: "2026-11-10T11:00:00Z"

REPO_ROOT = os.path.normpath(os.path.join(_here, "..", ".."))
OUT = os.path.join(REPO_ROOT, "apps", "pwa", "shipproof.json")


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

    # 3. Rotten evidence — the "trust me, it passed once" attack.
    #    A capability whose ONLY evidence is a payload the index says
    #    has drifted must fall to unsubstantiated, alert named.
    rot = {"id": "drill-rot", "title": "Rotten evidence", "layer": "x",
           "evidence": [{"kind": "determinism",
                         "ref": "a stamped demo payload",
                         "payload": "apps/pwa/tamperproof.json"}],
           "outstanding": []}
    rotten_index = {"payloads": [
        {"path": "apps/pwa/tamperproof.json", "status": "drift"}]}
    row = assess(index=rotten_index, entries=[rot])["capabilities"][0]
    drills.append({"attack": "evidence drifted after it was cited",
                   "expected": "unsubstantiated",
                   "verdict": row["verdict"],
                   "severity": "red" if row["verdict"] ==
                               "unsubstantiated" else "green",
                   "note": "voided evidence is named, never silently "
                           "dropped: %s"
                           % "; ".join("%s=%s" % (a["payload"],
                                                  a["status"])
                                       for a in
                                       row["evidence_alerts"])})

    # 4. Evidence the index never heard of — the wave-6 blind spot,
    #    replayed at the readiness layer.
    ghost = {"id": "drill-ghost", "title": "Ghost evidence",
             "layer": "x",
             "evidence": [{"kind": "determinism",
                           "ref": "a payload the index never registered",
                           "payload": "apps/pwa/ghost.json"}],
             "outstanding": []}
    row = assess(index={"payloads": []}, entries=[ghost])[
        "capabilities"][0]
    drills.append({"attack": "evidence the index never registered",
                   "expected": "unsubstantiated",
                   "verdict": row["verdict"],
                   "severity": "red" if row["verdict"] ==
                               "unsubstantiated" else "green",
                   "note": "an unregistered payload cannot "
                           "substantiate anything — a registry that "
                           "falls behind is a blind spot, here it "
                           "fails closed"})

    return drills


def run_demo():
    # The cross-check reads the REAL tree: every payload-named piece of
    # evidence is re-verified against the live proof index before the
    # gate speaks. Only statuses enter the payload — stable data.
    index = proofindex.verify_all(root=REPO_ROOT, generated_at=CLOCK())
    report = assess(index=index)
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
            "evidence_crosscheck_run":
                report["summary"]["evidence_crosscheck"]["run"],
            "all_evidence_verified":
                report["summary"]["evidence_crosscheck"].get(
                    "all_verified", False),
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
            "evidence_crosscheck": "a readiness claim is only as "
                                   "strong as its evidence — every "
                                   "determinism claim on this board "
                                   "names a stamped payload, and the "
                                   "gate re-checks those payloads "
                                   "against the live proof index "
                                   "before it speaks (ADR-0070)",
        },
        "versions": {"shipgate": SHIPGATE_VERSION,
                     "demo": DEMO_VERSION,
                     "proofindex": proofindex.PROOFINDEX_VERSION},
    }
    return _stamp(payload)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    payloadio.write_text(out, json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")


if __name__ == "__main__":
    main()
