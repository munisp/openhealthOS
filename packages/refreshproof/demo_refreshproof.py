"""demo_refreshproof.py — refreshproof-demo@1.0.0: deterministic export
of the refresh rail's proof surface (ADR-0081).

Synthetic fixtures only; production machinery (proofindex.refresh's
run_recipes with an INJECTED executor — the CLI passes subprocesses,
this demo passes a fixture executor, so the payload stays byte-stable).
Byte-for-byte reproducible, sha256-stamped under the compact contract,
registered in proofindex, gated by the meta exporter suites.

Demo story: the wave-19 freshness gate names WHAT is stale; the refresh
rail is the one-command fix — and gate and fix read ONE recipe book
(recipes.py). Five fixture recipes cover every fate: already fresh,
regenerated (changed paths named), runner-error (a raise is a verdict),
absent-after-write (a recipe that produces nothing is a defect), and a
payload with NO recipe — stated, and in the real tree impossible: the
coverage pin fails the suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))

import recipes  # noqa: E402
import refresh  # noqa: E402

DEMO_VERSION = "refreshproof-demo@1.0.0"
CLOCK = "2026-11-11T19:45:00Z"
OUT = os.path.join(_HERE, "..", "..", "apps", "pwa",
                   "refreshproof.json")

# The wave-20 state of the real tree, as data.
CENSUS = {
    "scope": ("all 33 proofindex-registered payloads via 26 recipes "
              "(21 file-kind, 5 hash-kind covering 12 payloads)"),
    "method": ("gate and rail read ONE recipe book (recipes.py); "
               "hash-family probe snippets are byte-identical to the "
               "determinism gate's — pinned by cross-check test"),
    "payloads": 33,
    "recipes": 26,
    "file_kind": 21,
    "hash_kind": 5,
    "hash_kind_payloads": 12,
    "recipe_less_payloads": ("none — the coverage pin fails the suite "
                             "on any registered payload without a "
                             "recipe"),
}

FIXTURE_FILES = {
    "fixtures/alpha.json": b'{"reading":"fresh","v":1}',
    "fixtures/beta.json": b'{"reading":"stale","v":1}',
    "fixtures/gamma.json": b'{"reading":"exporter-broken","v":1}',
    # delta.json is deliberately NOT on disk: the delta recipe runs
    # clean but writes nothing, so the rail must name it
    # absent-after-write — which is impossible if the file pre-exists.
    # epsilon.json is the recipe-less payload — stated, never run.
}

FIXTURE_RECIPES = [
    {"name": "alpha", "package": "fixture", "kind": "file",
     "write": "alpha", "paths": ["fixtures/alpha.json"]},
    {"name": "beta", "package": "fixture", "kind": "file",
     "write": "beta", "paths": ["fixtures/beta.json"]},
    {"name": "gamma", "package": "fixture", "kind": "file",
     "write": "gamma", "paths": ["fixtures/gamma.json"]},
    {"name": "delta", "package": "fixture", "kind": "file",
     "write": "delta", "paths": ["fixtures/delta.json"]},
]


def _stamp(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def run_demo():
    with tempfile.TemporaryDirectory() as td:
        for rel, data in FIXTURE_FILES.items():
            full = os.path.join(td, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as fh:
                fh.write(data)

        def executor(pkg, snippet):
            if snippet == "alpha":
                pass  # writes identical bytes — already fresh
            elif snippet == "beta":
                with open(os.path.join(td, "fixtures", "beta.json"),
                          "wb") as fh:
                    fh.write(b'{"reading":"stale","v":2}')
            elif snippet == "gamma":
                raise RuntimeError("fixture adapter refused to run")
            # delta: runs clean but writes nothing

        report = refresh.run_recipes(FIXTURE_RECIPES, root=td,
                                     executor=executor)

    got = {x["name"]: x["status"] for x in report["results"]}
    assert got == {"alpha": "already-fresh", "beta": "regenerated",
                   "gamma": "runner-error", "delta": "absent-after-write"}

    drills = [
        {"drill": "already fresh — the rail is a no-op on honest evidence",
         "recipe": "alpha", "verdict": "already-fresh",
         "severity": "green"},
        {"drill": "stale payload — regenerated, changed paths named",
         "recipe": "beta", "verdict": "regenerated", "severity": "green"},
        {"drill": "exporter down — a raise is a verdict, never a crash",
         "recipe": "gamma", "verdict": "runner-error",
         "severity": "red"},
        {"drill": "recipe writes nothing — a defect, named",
         "recipe": "delta", "verdict": "absent-after-write",
         "severity": "red"},
        {"drill": "payload with no recipe — stated; in the real tree "
                  "the coverage pin fails the suite",
         "recipe": "fixtures/epsilon.json", "verdict": "no-recipe",
         "severity": "amber"},
    ]

    body = {
        "version": DEMO_VERSION,
        "generated_at": CLOCK,
        "surface": "refreshproof",
        "title": ("The refresh rail — the freshness gate's one-command "
                  "fix, reading the same recipe book"),
        "census": CENSUS,
        "rail": {
            "module": "packages/proofindex/refresh.py",
            "recipe_book": "packages/proofindex/recipes.py",
            "seam": ("run_recipes(recipes, root=..., executor=...) — "
                     "the executor is INJECTED; the CLI supplies "
                     "subprocesses, demos supply fixtures"),
            "statuses": ["already-fresh", "regenerated", "runner-error",
                         "absent-after-write"],
        },
        "report": report,
        "drills": drills,
        "doctrine": [
            "A gate without a fix path is friction; a fix path without "
            "the gate's recipe book is drift waiting to happen.",
            "The rail regenerates everything in one run; regenerated "
            "paths are named with before/after digests.",
            "A recipe that runs clean but produces no payload is a "
            "defect — absent-after-write, red, named.",
            "Recipe-less payloads are impossible in the real tree: the "
            "coverage pin turns registry drift into a suite failure.",
        ],
    }
    body["sha256"] = _stamp(body)
    return body


def main():
    body = run_demo()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(body, fh, sort_keys=True, separators=(",", ":"))
    print("refreshproof: %d drills, %d recipes, sha %s -> %s"
          % (len(body["drills"]), body["census"]["recipes"],
             body["sha256"][:12], os.path.relpath(OUT)))


if __name__ == "__main__":
    main()
