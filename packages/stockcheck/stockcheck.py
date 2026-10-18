#!/usr/bin/env python3
"""stockcheck — claim-vs-dispense-vs-procurement reconciliation (ADR-0018).

Stock theft has two shapes, and both leave arithmetic evidence:

  - ghost dispensing: a claim says 30 tablets went to a patient; the
    pharmacy ledger says 0 were dispensed. The payer pays for air.
  - shrinkage: procurement delivered 10,000 tablets, dispensing moved
    6,000, and 3,500 walked out the back door to be sold privately.

This package reconciles three JSONL feeds per facility per drug:

  claims:      {"facility_id","item_code","quantity"}        (claim lines)
  dispensing:  {"facility_id","drug_code","quantity","status"} (pharmacy ledger)
  procurement: {"facility_id","drug_code","quantity"}        (receipts)

Verdicts are OK / REVIEW, never accusations — a variance is a question
("show us the paper trail"), not a verdict ("you stole"). Tolerances
are explicit because stock counts in the real world are never perfect.

CLI:
  python3 stockcheck.py reconcile --claims claims.jsonl \
      --dispensing dispense.jsonl --procurement proc.jsonl \
      [--tolerance-pct 2] [--tolerance-units 5]
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field


def read_jsonl(path: str) -> list[dict]:
    """Tolerant reader: malformed lines are skipped, a missing file is
    an honest empty feed — a facility with no procurement file has no
    procurement, not an error."""
    rows: list[dict] = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # a corrupt line never sinks the run
    except FileNotFoundError:
        pass
    return rows


@dataclass
class DrugRecon:
    facility_id: str
    drug_code: str
    claimed: int = 0
    dispensed: int = 0
    procured: int = 0
    flags: list[str] = field(default_factory=list)
    verdict: str = "OK"  # OK | REVIEW

    @property
    def claim_variance(self) -> int:
        """Claimed minus dispensed. Positive = billed but never moved
        stock — the ghost-dispensing question."""
        return self.claimed - self.dispensed

    @property
    def shrinkage(self) -> int:
        """Procured minus dispensed. Positive = stock that entered and
        never left through the front door."""
        return self.procured - self.dispensed


def _within(variance: int, base: int, tol_pct: float, tol_units: int) -> bool:
    return abs(variance) <= max(tol_units, base * tol_pct / 100.0)


def reconcile(claims: list[dict], dispensing: list[dict],
              procurement: list[dict], tol_pct: float = 2.0,
              tol_units: int = 5) -> list[DrugRecon]:
    by: dict[tuple[str, str], DrugRecon] = {}

    def bucket(facility: str, drug: str) -> DrugRecon:
        key = (facility or "UNKNOWN", drug or "UNKNOWN")
        if key not in by:
            by[key] = DrugRecon(facility_id=key[0], drug_code=key[1])
        return by[key]

    for c in claims:
        q = int(c.get("quantity", 0) or 0)
        bucket(c.get("facility_id", ""), c.get("item_code", c.get("drug_code", ""))).claimed += q
    for d in dispensing:
        if d.get("status", "dispensed") != "dispensed":
            continue  # pending/rejected requests moved no stock
        q = int(d.get("quantity", 0) or 0)
        bucket(d.get("facility_id", ""), d.get("drug_code", d.get("item_code", ""))).dispensed += q
    for p in procurement:
        q = int(p.get("quantity", 0) or 0)
        bucket(p.get("facility_id", ""), p.get("drug_code", p.get("item_code", ""))).procured += q

    out: list[DrugRecon] = []
    for r in by.values():
        # Question 1: did the payer get billed for stock that never moved?
        if r.claim_variance > 0 and not _within(r.claim_variance, r.dispensed, tol_pct, tol_units):
            r.flags.append(
                f"GHOST_DISPENSE?: claimed {r.claimed} units but pharmacy dispensed "
                f"{r.dispensed} — {r.claim_variance} units billed without a dispensing record")
        # Question 2: did stock enter and vanish?
        if r.shrinkage > 0 and not _within(r.shrinkage, r.dispensed, tol_pct, tol_units):
            r.flags.append(
                f"SHRINKAGE?: procured {r.procured} units, dispensed {r.dispensed} — "
                f"{r.shrinkage} units unaccounted (count the shelf, check the register)")
        # Question 3: dispensing without procurement — borrowed stock, or
        # a procurement feed that stopped reporting. Also a question.
        if r.dispensed > r.procured and r.procured > 0 and not _within(
                r.dispensed - r.procured, r.procured, tol_pct, tol_units):
            r.flags.append(
                f"UNPROCURED_DISPENSE?: dispensed {r.dispensed} against {r.procured} "
                f"procured — stock source undocumented")
        if r.flags:
            r.verdict = "REVIEW"
        out.append(r)
    out.sort(key=lambda r: (r.facility_id, r.drug_code))
    return out


def to_dict(r: DrugRecon) -> dict:
    return {
        "facility_id": r.facility_id, "drug_code": r.drug_code,
        "claimed": r.claimed, "dispensed": r.dispensed, "procured": r.procured,
        "claim_variance": r.claim_variance, "shrinkage": r.shrinkage,
        "verdict": r.verdict, "flags": r.flags,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stock reconciliation (ADR-0018)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rc = sub.add_parser("reconcile")
    rc.add_argument("--claims", required=True)
    rc.add_argument("--dispensing", required=True)
    rc.add_argument("--procurement", required=True)
    rc.add_argument("--tolerance-pct", type=float, default=2.0)
    rc.add_argument("--tolerance-units", type=int, default=5)
    args = ap.parse_args(argv)

    if args.cmd == "reconcile":
        rows = reconcile(read_jsonl(args.claims), read_jsonl(args.dispensing),
                         read_jsonl(args.procurement),
                         args.tolerance_pct, args.tolerance_units)
        report = {
            "reconciler": "openhealth-stockcheck@0.1.0",
            "tolerance": {"pct": args.tolerance_pct, "units": args.tolerance_units},
            "drugs": [to_dict(r) for r in rows],
            "review": sum(1 for r in rows if r.verdict == "REVIEW"),
            "ok": sum(1 for r in rows if r.verdict == "OK"),
            "note": "REVIEW is a question, not an accusation — produce the paper trail.",
        }
        print(json.dumps(report, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
