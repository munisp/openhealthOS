#!/usr/bin/env python3
"""OpenHealth OS — Nigerian Million-Claim Challenge harness.

Measures the adjudication pipeline against a national-scale workload:
one million claims is roughly a busy month across a state scheme. The
harness generates a deterministic synthetic claim stream, runs it through
the real adjudicator (not a mock), and reports throughput, latency
percentiles, decision distribution, and correctness invariants.

Truthful scoring is the point:

- the same seed always produces the same claim stream — runs are
  comparable across machines and commits;
- every number reported is measured wall time on the actual decision
  function — no cached verdicts, no skipped rules;
- correctness invariants are asserted, not assumed: every decision
  carries reason codes, every approval carries a payable amount, and
  money fields are internally consistent (payable = allowed - copay).

Usage:
    python3 million_claims.py --claims 1000000 [--seed 42] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

# The harness measures the real adjudicator — same seam the orchestrator
# shells out to in production.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "adjudicator"))
from adjudicate import BENEFIT, MEMBERS, TARIFF, adjudicate  # noqa: E402

SERVICE_DATE = "2026-10-17"

# Workload mix: the fraud/abuse cases are deliberately a minority —
# a benchmark that is 50% adversarial measures the wrong system.
MIX = [
    ("clean", 0.80),
    ("frequency_breach", 0.05),
    ("duplicate", 0.05),
    ("price_drift", 0.05),
    ("benefit_violation", 0.03),
    ("inactive_member", 0.02),
]


def generate_claims(n: int, seed: int) -> list[dict]:
    """Deterministic synthetic claim stream over the adjudicator fixtures."""
    rng = random.Random(seed)
    members = list(MEMBERS)
    active_members = [m for m in members if MEMBERS[m]["active"]]
    claims: list[dict] = []
    for i in range(n):
        r = rng.random()
        kind = "clean"
        acc = 0.0
        for name, weight in MIX:
            acc += weight
            if r <= acc:
                kind = name
                break
        member = rng.choice(active_members)
        scheme = MEMBERS[member]["scheme"]
        covered = BENEFIT[scheme]
        code = rng.choice(covered)
        qty = rng.randint(1, 2)
        history: list[dict] = []
        if kind == "frequency_breach":
            qty = rng.randint(3, 6)  # over the cap where one exists
            code = rng.choice(["MAL-RDT", "PARA-TAB"])
        elif kind == "duplicate":
            history = [{"member_id": member, "provider_id": "PHC-BENCH",
                        "service_date": "2026-10-10", "items": [{"code": code}]}]
        elif kind == "benefit_violation":
            others = [c for c in TARIFF if c not in covered]
            code = rng.choice(others) if others else code
        elif kind == "inactive_member":
            member = "NG-0003"  # fixture inactive member
        claimed = TARIFF.get(code, 1000) * qty
        if kind == "price_drift":
            claimed = int(claimed * rng.uniform(1.2, 2.0))
        claims.append({
            "claim_id": f"BENCH-{i:07d}",
            "encounter_id": f"BENC-{i:07d}",  # linkage gate: money follows encounters
            "member_id": member,
            "provider_id": "PHC-BENCH",
            "service_date": SERVICE_DATE,
            "items": [{"code": code, "qty": qty}],
            "claimed_ngn": claimed,
            "history": history,
            "_kind": kind,  # harness bookkeeping — stripped before scoring
        })
    return claims


def generate_encounters(claims: list[dict]) -> list[dict]:
    """The encounter evidence for a claim stream — one platform encounter
    per claim, matching member/facility/date, with presence strength.
    Money follows encounters (ADR-0018): a claim benchmark without
    encounters measures a strawman that the linkage gate rightly pends."""
    out: list[dict] = []
    for i, c in enumerate(claims):
        out.append({
            "encounter_id": c["encounter_id"],
            "patient_id": c["member_id"],
            "facility_id": c["provider_id"],
            "date": c["service_date"],
            "presence": "biometric" if i % 2 == 0 else "card",
        })
    return out


class _EncounterEnv:
    """Point the adjudicator's encounter seam at a generated JSONL file
    for the duration of a run; restore the previous env afterwards."""

    def __init__(self, encounters: list[dict]):
        self.encounters = encounters
        self._prev: str | None = None
        self._path = ""

    def __enter__(self):
        import os
        import tempfile
        fd, self._path = tempfile.mkstemp(prefix="bench-encounters-", suffix=".jsonl")
        with os.fdopen(fd, "w") as fh:
            for e in self.encounters:
                fh.write(json.dumps(e) + "\n")
        self._prev = os.environ.get("ENCOUNTER_FILE")
        os.environ["ENCOUNTER_FILE"] = self._path
        return self

    def __exit__(self, *exc):
        import os
        if self._prev is None:
            os.environ.pop("ENCOUNTER_FILE", None)
        else:
            os.environ["ENCOUNTER_FILE"] = self._prev
        try:
            os.unlink(self._path)
        except OSError:
            pass


def check_invariants(decision: dict) -> list[str]:
    """Correctness invariants every adjudication event must satisfy."""
    problems = []
    if decision["decision"] not in ("APPROVE", "PEND", "DENY"):
        problems.append(f"unknown decision {decision['decision']!r}")
    if not decision["reasons"]:
        problems.append("decision without reason codes — silent verdict")
    if decision["decision"] == "APPROVE":
        if decision["payable_ngn"] != decision["allowed_ngn"] - decision["copay_ngn"]:
            problems.append("payable != allowed - copay — money inconsistency")
        if decision["allowed_ngn"] <= 0:
            problems.append("approval with zero allowed amount")
    return problems


def run(claims: list[dict]) -> dict:
    latencies_ms: list[float] = []
    distribution = {"APPROVE": 0, "PEND": 0, "DENY": 0}
    violations: list[str] = []
    with _EncounterEnv(generate_encounters(claims)):
        started = time.perf_counter()
        for claim in claims:
            claim.pop("_kind", None)
            t0 = time.perf_counter()
            decision = adjudicate(claim)
            latencies_ms.append((time.perf_counter() - t0) * 1000)
            distribution[decision["decision"]] += 1
            for problem in check_invariants(decision):
                violations.append(f"{claim['claim_id']}: {problem}")
        wall_s = time.perf_counter() - started
    latencies_ms.sort()
    n = len(claims)

    def pct(p: float) -> float:
        return latencies_ms[min(n - 1, int(p * n))]

    return {
        "claims": n,
        "wall_seconds": round(wall_s, 3),
        "throughput_claims_per_sec": round(n / wall_s, 1),
        "latency_ms": {
            "p50": round(pct(0.50), 3),
            "p95": round(pct(0.95), 3),
            "p99": round(pct(0.99), 3),
            "mean": round(statistics.fmean(latencies_ms), 3),
        },
        "decision_distribution": distribution,
        "invariant_violations": violations[:50],  # cap the report, not the check
        "invariant_violation_count": len(violations),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Nigerian Million-Claim Challenge")
    parser.add_argument("--claims", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    claims = generate_claims(args.claims, args.seed)
    report = run(claims)
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n")
    # Non-zero exit on any correctness violation — a fast wrong engine
    # is a failed benchmark, whatever the throughput number says.
    return 1 if report["invariant_violation_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
