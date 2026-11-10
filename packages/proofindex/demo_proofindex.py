"""proofindex-demo@1.1.0 — the proof-index evidence surface.

Re-verifies every stamped evidence payload in the tree and renders the
verdicts as one officer-readable board. Same doctrine as every demo
exporter before it: a demo you cannot reproduce is a claim, not
evidence — fixed clock, registry order, byte-identical reruns, and a
sha256 stamp of its own (compact contract).

The drills section proves the gate itself works: five attacks (drift,
non-canonical stamp, contract mismatch, absence, and an unregistered
stamped payload — the wave-6 blind spot, replayed in a tmpdir) each
produce their named verdict. No tempdir paths enter the payload —
drills run on in-memory dicts or throwaway directories, never the
committed files.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from proofindex import (CONTRACTS, PAYLOADS, PROOFINDEX_VERSION,
                        coverage, verify_all, verify_dict)

DEMO_VERSION = "proofindex-demo@1.1.0"
CLOCK = lambda: "2026-11-10T09:00:00Z"

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "proofindex.json")

_REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__),
                                           "..", ".."))


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**payload, "sha256": digest}


def _drills() -> list:
    """Four attacks on the gate, in memory — each must produce its
    named verdict. The committed files are never touched."""
    drills = []

    # 1. DRIFT — bytes changed after stamping.
    entry = dict(PAYLOADS[0])          # tamperproof.json, compact
    with open(os.path.join(_REPO_ROOT, entry["path"]),
              encoding="utf-8") as fh:
        genuine = json.load(fh)
    tampered = dict(genuine)
    tampered["gate"] = dict(genuine["gate"])
    tampered["gate"]["sweep_ok"] = True    # a claim the stamp never made
    v = verify_dict(tampered, entry)
    drills.append({"attack": "content drift",
                   "expected": "drift", "verdict": v["status"],
                   "severity": v["severity"],
                   "note": "one key added after stamping — recomputed "
                           "digest matches neither contract"})

    # 2. NON-CANONICAL STAMP — uppercase the digest string.
    twin = dict(genuine)
    twin["sha256"] = genuine["sha256"].upper()
    v = verify_dict(twin, entry)
    drills.append({"attack": "uppercase stamp",
                   "expected": "non-canonical-stamp",
                   "verdict": v["status"], "severity": v["severity"],
                   "note": "same hex, wrong STRING — the string is the "
                           "contract (row 201 at the meta layer)"})

    # 3. CONTRACT MISMATCH — restamp the body under the other contract.
    body = {k: v for k, v in genuine.items() if k != "sha256"}
    restamped = dict(body)
    restamped["sha256"] = CONTRACTS["spaced-utf8"](body)
    v = verify_dict(restamped, entry)
    drills.append({"attack": "foreign-contract stamp",
                   "expected": "contract-mismatch",
                   "verdict": v["status"], "severity": v["severity"],
                   "note": "evidence intact under spaced-utf8 but the "
                           "registry records compact — amber, stated"})

    # 4. ABSENCE — a registered path that is not on disk.
    ghost = {"path": "apps/pwa/no-such-payload.json",
             "contract": "compact",
             "exporter": "packages/proofindex/demo_proofindex.py",
             "title": "Ghost payload"}
    from proofindex import verify_payload
    v = verify_payload(ghost, root=_REPO_ROOT)
    drills.append({"attack": "missing file",
                   "expected": "absent", "verdict": v["status"],
                   "severity": v["severity"],
                   "note": "absence is a stated verdict, never a crash, "
                           "never a green"})

    # 5. UNREGISTERED — a stamped payload the registry does not know
    #    (the wave-6 blind spot: anchorproof.json spent one wave
    #    stamped but unindexed). Replayed in a throwaway directory;
    #    its path never enters this payload.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "apps", "pwa"))
        rogue = {"claim": "stamped but never registered"}
        rogue["sha256"] = CONTRACTS["compact"](rogue)
        with open(os.path.join(td, "apps", "pwa", "rogue.json"),
                  "w") as fh:
            json.dump(rogue, fh)
        cov = coverage(root=td)
    drills.append({"attack": "unregistered stamped payload",
                   "expected": "unregistered",
                   "verdict": ("unregistered" if cov["unregistered"]
                               and not cov["complete"]
                               else "missed"),
                   "severity": ("red" if cov["unregistered"]
                                else "green"),
                   "note": "the scan names every stamped file the "
                           "registry does not know — a registry that "
                           "falls behind silently is a blind spot"})

    return drills


def run_demo():
    report = verify_all(root=_REPO_ROOT, generated_at=CLOCK())
    drills = _drills()
    payload = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK(),
        "registry": report,
        "drills": drills,
        "flags": {
            "all_verified": report["summary"]["all_verified"],
            "coverage_complete": report["coverage"]["complete"],
            "drills_named": all(d["verdict"] == d["expected"]
                                for d in drills),
        },
        "self_integrity": {
            "contract": "compact",
            "note": "the index cannot stamp its own stamp — this "
                    "payload's sha256 is pinned by TestDemoProofindex "
                    "determinism + stamp-contract tests, and proofindex "
                    "is declared in SELF_EXCLUDED as data; the coverage "
                    "scan must account for every stamped file on disk "
                    "as registered or self-excluded, never silently "
                    "skipped",
        },
        "versions": {"proofindex": PROOFINDEX_VERSION,
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
