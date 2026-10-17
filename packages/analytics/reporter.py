#!/usr/bin/env python3
"""OpenHealth OS — pilot-gates reporter.

Computes the §6.6 pilot gates from live telemetry: pulls the applied
event log and dedup health from sync-ingest (/events, /metrics) and runs
the gate functions in gates.py. One command answers the pilot question —
are we ready to scale? — with numbers, not vibes.

Usage:
    python3 reporter.py --ingest http://127.0.0.1:8443 \
        --facilities 25 --days 90 --trained-staff 120

Exit code is 0 when every gate passes, 1 otherwise — safe to wire into a
go/no-go checklist or CI.
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from gates import (
    claims_turnaround,
    offline_continuity,
    staff_weekly_active,
    stockout_visibility,
    sync_reliability,
)


def fetch_json(url: str, timeout: float = 10.0):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def report(ingest_url: str, facilities: int, days: int, trained_staff: int) -> dict:
    events = fetch_json(ingest_url.rstrip("/") + "/events") or []
    metrics = fetch_json(ingest_url.rstrip("/") + "/metrics")
    gates = {
        "offline_continuity": offline_continuity(events),
        "sync_reliability": sync_reliability(events),
        "claims_turnaround": claims_turnaround(events),
        "stockout_visibility": stockout_visibility(events, facilities, days),
        "staff_weekly_active": staff_weekly_active(events, trained_staff, max(1, days // 7)),
    }
    passed = sum(1 for g in gates.values() if g.get("pass"))
    return {
        "source": ingest_url,
        "ingest_health": {
            "events_received": metrics.get("events_received"),
            "events_applied": metrics.get("events_applied"),
            "duplicates_dropped": metrics.get("duplicates_dropped"),
        },
        "gates": gates,
        "gates_passed": passed,
        "gates_total": len(gates),
        "ready_to_scale": passed == len(gates),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pilot-gates reporter (spec §6.6)")
    parser.add_argument("--ingest", default="http://127.0.0.1:8443",
                        help="sync-ingest base URL")
    parser.add_argument("--facilities", type=int, default=25)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--trained-staff", type=int, default=120)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    result = report(args.ingest, args.facilities, args.days, args.trained_staff)
    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(result, fh, indent=2)
            fh.write("\n")
    return 0 if result["ready_to_scale"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
