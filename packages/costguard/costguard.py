"""OpenHealth OS — cost guardrails (Kubecost-style budgets, CI-enforced).

Per-state hosting cost must be self-documenting and self-policing
(integration guarantee #4). costguard reads the Kubernetes manifests that
ARE the deployment, prices their CPU/memory requests and limits against a
checked-in rate card, and fails CI when a state plane exceeds its monthly
budget. The budget is code; exceeding it is a build error, not a surprise
invoice.

Rate card (USD, on-prem/Nigerian-cloud realistic):
    vCPU-month  $12     GB-memory-month  $1.8     GB-storage-month  $0.08

CLI:
    python3 costguard.py check --manifests infra/k8s/ --budget-usd 150
Exit 0 within budget, 1 over budget.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import yaml  # PyYAML (MIT) — tool dependency, never shipped in services

RATE_CARD = {"vcpu_month": 12.0, "gb_mem_month": 1.8, "gb_storage_month": 0.08}


def parse_cpu(s) -> float:
    """'500m' -> 0.5, '2' -> 2.0"""
    if s is None:
        return 0.0
    s = str(s)
    return float(s[:-1]) / 1000.0 if s.endswith("m") else float(s)


def parse_mem(s) -> float:
    """'256Mi' -> 0.25 GB, '1Gi' -> 1.0 GB"""
    if s is None:
        return 0.0
    s = str(s)
    for suffix, mult in (("Gi", 1.0), ("Mi", 1 / 1024), ("Ki", 1 / 1024**2),
                         ("G", 1.0), ("M", 1 / 1000)):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    return float(s) / 1024**3  # raw bytes


def deployment_cost(doc: dict, rates: dict) -> dict:
    """Monthly cost of one Deployment (requests × replicas — what you
    reserve is what you pay, limits are headroom)."""
    spec = doc.get("spec", {})
    replicas = spec.get("replicas", 1)
    cpu = mem = 0.0
    for c in (spec.get("template", {}).get("spec", {}).get("containers") or []):
        req = (c.get("resources") or {}).get("requests") or {}
        cpu += parse_cpu(req.get("cpu"))
        mem += parse_mem(req.get("memory"))
    return {
        "name": doc.get("metadata", {}).get("name", "?"),
        "replicas": replicas,
        "vcpu": cpu * replicas,
        "gb_mem": mem * replicas,
        "usd_month": round(replicas * (cpu * rates["vcpu_month"]
                                       + mem * rates["gb_mem_month"]), 2),
    }


def scan(manifests_dir: str, rates: dict | None = None) -> list[dict]:
    rates = rates or RATE_CARD
    costs = []
    for path in sorted(glob.glob(os.path.join(manifests_dir, "**", "*.yaml"),
                                 recursive=True)):
        for doc in yaml.safe_load_all(open(path)):
            if isinstance(doc, dict) and doc.get("kind") == "Deployment":
                costs.append(deployment_cost(doc, rates))
    return costs


def check(manifests_dir: str, budget_usd: float, rates: dict | None = None) -> tuple[list[dict], float, bool]:
    costs = scan(manifests_dir, rates)
    total = round(sum(c["usd_month"] for c in costs), 2)
    return costs, total, total <= budget_usd


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="per-state hosting budget gate")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--manifests", required=True)
    c.add_argument("--budget-usd", type=float, required=True)
    args = ap.parse_args(argv)

    costs, total, ok = check(args.manifests, args.budget_usd)
    for c_ in costs:
        print(f"  {c_['name']:24s} {c_['replicas']}x  "
              f"{c_['vcpu']:.2f} vCPU  {c_['gb_mem']:.2f} GB  ${c_['usd_month']}/mo")
    print(f"total: ${total}/mo vs budget ${args.budget_usd}/mo")
    if not ok:
        print("OVER BUDGET — reduce replicas/requests or raise the budget in CI")
        return 1
    print("within budget")
    return 0


if __name__ == "__main__":
    sys.exit(main())
