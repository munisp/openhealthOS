"""OpenHealth OS — lakehouse pipeline (bronze / silver / gold).

The spec's data plane (technical spec §Data/lakehouse) is Parquet + Delta on
MinIO with Flink/Spark in production. The reference pipeline implements the
same medallion contracts stdlib-first:

    bronze  raw events, partitioned immutable JSONL     (append-only truth)
    silver  canonical per-entity records                (cleaned, typed)
    gold    decision-ready aggregates                   (dashboards, DHIS2)

Parquet is used for silver/gold when pyarrow is installed
(`pip install -r requirements.txt` — Apache-2.0); otherwise the same
partitions are written as JSONL. The production swap to Delta/Spark changes
the writer, never the contracts.

CLI:
    python3 lakehouse.py build --events sync_events.jsonl --out lake/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAVE_PARQUET = True
except ImportError:
    HAVE_PARQUET = False


def _day(ts: str) -> str:
    """Partition key from an ISO timestamp; undated events land in 'unknown'."""
    try:
        return ts[:10]
    except TypeError:
        return "unknown"


def write_partition(rows: list[dict], path: str) -> str:
    """Write rows as Parquet when available, else JSONL. Returns the path."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if HAVE_PARQUET and rows:
        pq.write_table(pa.Table.from_pylist(rows), path + ".parquet")
        return path + ".parquet"
    with open(path + ".jsonl", "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    return path + ".jsonl"


def bronze(events: list[dict], out: str) -> int:
    """Append-only raw partitions by event day."""
    parts: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        parts[_day(ev.get("ts", ""))].append(ev)
    n = 0
    for day, rows in sorted(parts.items()):
        write_partition(rows, os.path.join(out, "bronze", f"day={day}", "events"))
        n += len(rows)
    return n


class FreeTextGateError(Exception):
    """Free text tried to cross into silver without a de-id scrubber."""


def silver(events: list[dict], out: str, scrub=None) -> int:
    """Canonical records: one silver table per entity-ish event type.

    Free-text doctrine (ADR-0065): an event declaring
    ``free_text: [payload keys]`` carries narrative PHI. Those fields may
    enter silver ONLY through a de-identification scrubber (packages/
    mednlp.deid or the live OpenMed sidecar). No scrubber => raise, never
    pass raw narrative into the analytical plane."""
    tables: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        etype = ev.get("type", "unknown")
        payload = ev.get("payload") or {}
        fields = {k: v for k, v in payload.items()
                  if isinstance(v, (str, int, float, bool))}
        declared = ev.get("free_text") or []
        if declared:
            if scrub is None:
                raise FreeTextGateError(
                    f"event {ev.get('id', '?')} declares free_text {declared} "
                    "but no scrubber was provided")
            for k in declared:
                if isinstance(fields.get(k), str):
                    fields[k] = scrub(fields[k])
        rec = {
            "id": ev.get("id", ""),
            "facility": ev.get("facility", ""),
            "ts": ev.get("ts", ""),
            "type": etype,
            **fields,
        }
        tables[etype].append(rec)
    n = 0
    for etype, rows in sorted(tables.items()):
        write_partition(rows, os.path.join(out, "silver", f"type={etype}", "records"))
        n += len(rows)
    return n


def gold(events: list[dict], out: str) -> dict:
    """Decision-ready aggregates — the dashboards' and DHIS2 bridge's input."""
    by_facility: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    claim_value: dict[str, float] = defaultdict(float)
    for ev in events:
        by_facility[ev.get("facility", "?")] += 1
        by_type[ev.get("type", "?")] += 1
        if ev.get("type") == "claim_decided":
            p = ev.get("payload") or {}
            scheme = p.get("scheme", "?")
            claim_value[scheme] += float(p.get("payable_ngn", 0) or 0)
    agg = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "events_by_facility": dict(sorted(by_facility.items())),
        "events_by_type": dict(sorted(by_type.items())),
        "claim_value_by_scheme_ngn": dict(sorted(claim_value.items())),
    }
    os.makedirs(os.path.join(out, "gold"), exist_ok=True)
    with open(os.path.join(out, "gold", "aggregates.json"), "w") as fh:
        json.dump(agg, fh, indent=2, sort_keys=True)
    return agg


def build(events: list[dict], out: str, scrub=None) -> dict:
    nb = bronze(events, out)
    ns = silver(events, out, scrub=scrub)
    agg = gold(events, out)
    return {"bronze_rows": nb, "silver_rows": ns, "gold": agg,
            "format": "parquet" if HAVE_PARQUET else "jsonl"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="medallion lakehouse pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--events", required=True, help="JSONL of sync events")
    b.add_argument("--out", required=True, help="lake root directory")
    args = ap.parse_args(argv)

    events = []
    with open(args.events) as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    result = build(events, args.out)
    print(json.dumps({k: v for k, v in result.items() if k != "gold"}, indent=2))
    print(f"gold aggregates -> {args.out}/gold/aggregates.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
