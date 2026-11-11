"""demo_freshproof.py — freshproof-demo@1.0.0: deterministic export of
the freshness gate's proof surface (ADR-0080).

Synthetic fixtures only; production machinery (proofindex.freshness_report
with injected runners). Byte-for-byte reproducible, sha256-stamped under
the compact contract, registered in proofindex, gated by the meta
exporter-determinism AND exporter-freshness suites.

Demo story: the wave-17 blind spot, replayed on a synthetic fixture so
the demo itself stays byte-stable. Five payloads: one fresh (control),
one stale after a simulated dependency bump (the geointproof lesson),
one whose exporter is down, one with no deterministic exporter at all
(stated, amber — never hidden), one whose committed file is absent.
The gate names every one; silence was the defect, the verdict is the
fix.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "payloadio"))
import payloadio

import proofindex  # noqa: E402

DEMO_VERSION = "freshproof-demo@1.0.0"
CLOCK = "2026-11-11T19:00:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa", "freshproof.json")

# The wave-19 census of the REAL tree, as data. Scope and method are
# stated — a census that does not state its scope is a claim.
CENSUS = {
    "scope": ("all 29 proofindex-registered payloads via all 22 "
              "meta-gate exporters (python packages + pwa surfaces)"),
    "method": ("fresh export per exporter compared to committed bytes "
               "(file family, 17 payloads) or to committed sha256 "
               "digests (hash family, 12 payloads)"),
    "payloads": 29,
    "file_family": 17,
    "hash_family": 12,
    "fresh": 28,
    "stale": 1,
    "stale_named": ["apps/pwa/geointproof.json"],
    "resolution": ("geointproof.json regenerated (routing@1.2.0) and "
                   "committed in this wave; TestExporterFreshness makes "
                   "the next silent stale-dating red at suite time"),
    "this_wave_regenerations": ["apps/pwa/shipproof.json"],
    "this_wave_reason": ("shipproof embeds the proofindex version in its "
                        "versions member; the 1.2.0 bump legitimately "
                        "regenerates it IN this wave — exactly the "
                        "discipline the gate now enforces"),
}

# Synthetic fixture registry — five payloads, five fates. Paths are
# fixture-relative; no tmpdir path ever enters the payload.
FIXTURE_ENTRIES = [
    {"path": "fixtures/alpha.json", "contract": "compact",
     "exporter": "fixture/alpha", "title": "Control — fresh"},
    {"path": "fixtures/beta.json", "contract": "compact",
     "exporter": "fixture/beta", "title": "Dependency bump — stale"},
    {"path": "fixtures/gamma.json", "contract": "compact",
     "exporter": "fixture/gamma", "title": "Exporter down — runner-error"},
    {"path": "fixtures/delta.json", "contract": "compact",
     "exporter": "fixture/delta", "title": "Manual discipline — no-exporter"},
    {"path": "fixtures/epsilon.json", "contract": "compact",
     "exporter": "fixture/epsilon", "title": "Missing evidence — absent"},
]

_FIXTURE_BYTES = {
    "fixtures/alpha.json": b'{"reading":"controlled","version":1}',
    "fixtures/beta.json": b'{"reading":"stamped-at-v1","version":1}',
    "fixtures/gamma.json": b'{"reading":"exporter-broken","version":1}',
    "fixtures/delta.json": b'{"reading":"hand-refreshed","version":1}',
    # epsilon is deliberately never written — absence is the drill.
}


def _runners(fixture_root):
    """Injected runners over the synthetic fixture. proofindex never
    spawns a subprocess; here the 'executor' is a dict lookup — the
    seam is what matters, and it is the seam the real meta gate uses."""

    def fresh(entry):
        path = entry["path"]
        if path == "fixtures/alpha.json":
            return _FIXTURE_BYTES[path]               # identical bytes
        if path == "fixtures/beta.json":
            return b'{"reading":"stamped-at-v1","version":2}'  # bumped
        if path == "fixtures/gamma.json":
            raise RuntimeError("fixture adapter refused to run")
        if path == "fixtures/delta.json":
            return None                                # no exporter
        raise AssertionError("no runner for " + path)  # epsilon: absent

    return fresh


def _stamp(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def run_demo():
    """Build the fixture, run the REAL freshness gate over it with
    injected runners, and return the stamped payload."""
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "fixtures"))
        for rel, data in _FIXTURE_BYTES.items():
            with open(os.path.join(td, rel), "wb") as fh:
                fh.write(data)
        report = proofindex.freshness_report(
            _runners(td), root=td, payloads=FIXTURE_ENTRIES)

    drills = [
        {"drill": "control — committed bytes equal a fresh export",
         "path": "fixtures/alpha.json", "verdict": "fresh",
         "severity": "green"},
        {"drill": "dependency bump — machinery moved, evidence did not",
         "path": "fixtures/beta.json", "verdict": "stale",
         "severity": "red"},
        {"drill": "exporter down — the failure is a verdict, not a crash",
         "path": "fixtures/gamma.json", "verdict": "runner-error",
         "severity": "red"},
        {"drill": "no deterministic exporter — manual discipline stated",
         "path": "fixtures/delta.json", "verdict": "no-exporter",
         "severity": "amber"},
        {"drill": "committed file missing — absence stated, never faked",
         "path": "fixtures/epsilon.json", "verdict": "absent",
         "severity": "red"},
    ]
    # The report IS the evidence: pin that the gate produced exactly the
    # drilled verdicts, in registry order.
    assert [v["status"] for v in report["payloads"]] == [
        "fresh", "stale", "runner-error", "no-exporter", "absent"]

    body = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK,
        "surface": "freshproof",
        "title": ("The freshness gate — committed evidence equals "
                  "today's export, or the payload is named stale"),
        "census": CENSUS,
        "gate": {
            "library": proofindex.PROOFINDEX_VERSION,
            "seam": ("freshness_report(fresh, root=..., payloads=...) — "
                     "the runner is INJECTED; proofindex never spawns "
                     "a subprocess"),
            "verdicts": ["fresh", "stale", "runner-error",
                         "no-exporter", "absent"],
        },
        "report": report,
        "drills": drills,
        "doctrine": [
            "Determinism proves run-to-run sameness; freshness proves "
            "the committed evidence IS the current machinery's output.",
            "A stale payload is not drift — its own stamp still "
            "verifies. Only a freshness gate sees that the machinery "
            "has moved.",
            "A payload with no deterministic exporter is stated amber, "
            "never silently treated as fresh.",
            "The runner is injected like a clock: the library stays "
            "stdlib-only and testable; the meta gate supplies the "
            "subprocess.",
        ],
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    payloadio.write_text(OUT, json.dumps(body, sort_keys=True, separators=(",", ":")))
    print("freshproof: %d drills, %d stale in census, sha %s -> %s"
          % (len(body["drills"]), body["census"]["stale"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
