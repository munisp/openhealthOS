#!/usr/bin/env python3
"""forensics — scan-pattern anomaly detection over the custody spine
(ADR-0055).

The custody log records every scan; this package answers "which scans
don't make physical sense". Three detectors, all evidence-shaped (an
anomaly is a question for an auditor, never an accusation):

  impossible_travel — same asset scanned at two different facilities
      within --travel-window minutes. An MRI does not teleport between
      LGAs; one of the two scans is a photocopied tag.

  scan_burst — one actor scanning > --burst-max tags within a minute.
      Real stocktake sweeps have human rhythm; a script replaying a
      leaked tag sheet does not.

  stale_scan — scan events where the tag was already flagged stale
      (past the 5-year re-issue horizon). One stale scan is a worn tag;
      a pattern of them is a re-label programme not happening.

Input: custody JSONL (the equipment-service custody_events export shape):
  {"at":"2026-10-20T09:00:00Z","asset":"EQ-1","facility":"F1",
   "action":"locate","actor":"n1","location":"ward-2","stale_tag":false}

CLI:
  python3 forensics.py custody.jsonl [--travel-window 60] [--burst-max 10]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime


def _ts(s: str) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def read_events(path: str) -> list[dict]:
    out: list[dict] = []
    try:
        with open(path) as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    print(f"forensics: skipping malformed line {ln}", file=sys.stderr)
                    continue
                if row.get("asset") and row.get("at"):
                    out.append(row)
    except FileNotFoundError:
        print(f"forensics: {path} not found — empty spine", file=sys.stderr)
    return out


def impossible_travel(events: list[dict], window_min: int = 60) -> list[dict]:
    by_asset: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        t = _ts(e.get("at", ""))
        if t is not None:
            by_asset[e["asset"]].append((t, e))
    findings: list[dict] = []
    for asset, rows in by_asset.items():
        rows.sort()
        for (t1, e1), (t2, e2) in zip(rows, rows[1:]):
            if e1.get("facility") != e2.get("facility") and (t2 - t1) <= window_min * 60:
                findings.append({
                    "type": "impossible_travel", "asset": asset,
                    "detail": (f"{e1.get('facility')} @ {e1.get('at')} → "
                               f"{e2.get('facility')} @ {e2.get('at')} "
                               f"({int((t2 - t1) / 60)}min apart)"),
                    "question": "which scan saw the real asset — and which saw a photocopy?",
                })
    return findings


def scan_bursts(events: list[dict], burst_max: int = 10) -> list[dict]:
    by_actor: dict[str, list[float]] = defaultdict(list)
    for e in events:
        t = _ts(e.get("at", ""))
        if t is not None and e.get("actor"):
            by_actor[e["actor"]].append(t)
    findings: list[dict] = []
    for actor, times in by_actor.items():
        times.sort()
        for i in range(len(times)):
            j = i
            while j < len(times) and times[j] - times[i] <= 60:
                j += 1
            if j - i > burst_max:
                findings.append({
                    "type": "scan_burst", "actor": actor,
                    "detail": f"{j - i} scans within 60s starting {datetime.utcfromtimestamp(times[i]).isoformat()}Z",
                    "question": "human sweep or a replayed tag sheet?",
                })
                break  # one finding per actor is enough to ask the question
    return findings


def stale_scans(events: list[dict]) -> list[dict]:
    out = [e for e in events if e.get("stale_tag")]
    if not out:
        return []
    by_fac: dict[str, int] = defaultdict(int)
    for e in out:
        by_fac[e.get("facility", "?")] += 1
    return [{
        "type": "stale_scans",
        "detail": f"{len(out)} scans on stale tags; facilities: {dict(sorted(by_fac.items()))}",
        "question": "is the re-label programme running?",
    }]


def analyze(events: list[dict], travel_window: int = 60, burst_max: int = 10) -> dict:
    findings = (impossible_travel(events, travel_window)
                + scan_bursts(events, burst_max)
                + stale_scans(events))
    return {
        "events": len(events),
        "findings": findings,
        "clean": not findings,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="forensics")
    ap.add_argument("custody", help="custody JSONL export")
    ap.add_argument("--travel-window", type=int, default=60)
    ap.add_argument("--burst-max", type=int, default=10)
    args = ap.parse_args(argv)
    report = analyze(read_events(args.custody), args.travel_window, args.burst_max)
    print(json.dumps(report, indent=2))
    return 0 if report["clean"] else 1  # findings exit non-zero: CI/audit hook


if __name__ == "__main__":
    raise SystemExit(main())
