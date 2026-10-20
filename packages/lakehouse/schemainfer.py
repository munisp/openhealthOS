"""Lakehouse schema inference — normalize bronze JSONL by evidence, not by hand.

Insight adopted clean-room from foxtrotcommunications/forge-core
(Apache-2.0): nested event JSON should be decomposed into typed,
normalized silver tables by INFERENCE over the data, not by hand-edited
flattening code that rots every time a service adds a field. Forge-core
targets dbt/BigQuery/Snowflake/Databricks; we run a stdlib medallion
pipeline, so this implements the idea against our own contracts.

What it does:
  1. Scans a bronze JSONL partition (any volume; sampling is explicit).
  2. Infers per-field: type (string|number|bool|timestamp|array|object),
     presence rate, nullability, and nesting path.
  3. Emits a silver schema proposal: flat scalar columns stay in the
     parent table; repeated nested objects/arrays become CHILD tables
     with a parent key (1NF — the forge-core normalization move);
     sparse fields (<min_presence) are flagged as optional so silver
     writers don't hard-depend on them.
  4. Diff against the previous proposal: fields appearing/disappearing/
     changing type are listed, so a producer change shows up as a loud
     schema diff in review instead of a silent silver-column NULL drift.

CLI:
    python3 schemainfer.py infer EVENTS.jsonl [--sample 5000] [--min-presence 0.05]
    python3 schemainfer.py diff OLD.json NEW.json

Stdlib only (ADR-0005). Deterministic: same input → same schema.
"""
from __future__ import annotations

import argparse
import json
import re
import sys



ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _type_of(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        if ISO_TS.match(v):
            return "timestamp"
        if ISO_DATE.match(v):
            return "date"
        return "string"
    if isinstance(v, list):
        return "array"
    return "object"


def _merge(types: set[str]) -> str:
    """Widening: null is dropped; number absorbs nothing; mixed → string
    (the safest silver column; a strict type on mixed data drops rows)."""
    types = {t for t in types if t != "null"}
    if not types:
        return "string"
    if len(types) == 1:
        return types.pop()
    if types <= {"timestamp", "date", "string"}:
        return "string"
    return "string"  # deliberately conservative — see module docstring


def infer(events_path: str, sample: int | None, min_presence: float) -> dict:
    """Walk bronze events; build field stats keyed by dot-path."""
    stats: dict[str, dict] = {}

    def visit(prefix: str, obj: dict, n: int):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            st = stats.setdefault(path, {"types": set(), "seen": 0, "null": 0})
            st["seen"] += 1
            if v is None:
                st["null"] += 1
            else:
                st["types"].add(_type_of(v))
                if isinstance(v, dict):
                    visit(path, v, n)

    n = 0
    with open(events_path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            visit("", json.loads(line), n)
            n += 1
            if sample and n >= sample:
                break

    fields = {}
    for path, st in sorted(stats.items()):
        t = _merge(st["types"])
        presence = st["seen"] / n if n else 0
        fields[path] = {
            "type": t,
            "presence": round(presence, 4),
            "nullable": st["null"] > 0 or presence < 1.0,
            # 1NF: any container becomes a child-table candidate;
            # scalars stay in the parent.
            "child_table": t in ("object", "array"),
            "optional": presence < min_presence,
        }
    return {"source": events_path, "events_scanned": n,
            "min_presence": min_presence, "fields": fields}


def diff(old: dict, new: dict) -> dict:
    """Loud schema drift: added / removed / retyped fields."""
    out = {"added": [], "removed": [], "retyped": []}
    of, nf = old["fields"], new["fields"]
    for p in sorted(set(nf) - set(of)):
        out["added"].append({"field": p, "type": nf[p]["type"],
                             "presence": nf[p]["presence"]})
    for p in sorted(set(of) - set(nf)):
        out["removed"].append({"field": p, "type": of[p]["type"]})
    for p in sorted(set(of) & set(nf)):
        if of[p]["type"] != nf[p]["type"]:
            out["retyped"].append({"field": p, "was": of[p]["type"],
                                   "now": nf[p]["type"]})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="bronze JSONL schema inference")
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("infer")
    i.add_argument("events")
    i.add_argument("--sample", type=int)
    i.add_argument("--min-presence", type=float, default=0.05)
    d = sub.add_parser("diff")
    d.add_argument("old")
    d.add_argument("new")
    args = ap.parse_args(argv)

    if args.cmd == "infer":
        json.dump(infer(args.events, args.sample, args.min_presence),
                  sys.stdout, indent=1, sort_keys=True)
        print()
        return 0
    out = diff(json.load(open(args.old)), json.load(open(args.new)))
    json.dump(out, sys.stdout, indent=1, sort_keys=True)
    print()
    return 1 if (out["removed"] or out["retyped"]) else 0  # drift = loud exit


if __name__ == "__main__":
    raise SystemExit(main())
