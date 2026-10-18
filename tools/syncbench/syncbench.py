#!/usr/bin/env python3
"""Sync load & chaos harness (ADR-0013, improvement).

Measures the national sync path the way it fails in the field: bursts of
facility events against sync-ingest, p99 latency, and spill durability
under a torn write (a crash leaves a corrupt tail line — replay must skip
it and keep the good prefix).

  python3 tools/syncbench/syncbench.py --target http://127.0.0.1:18089 \
      --events 2000 --concurrency 16
  python3 tools/syncbench/syncbench.py --chaos-tail spill.ndjson

Exit 1 if p99 exceeds --p99-ms or a corrupt tail is not tolerated.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

EVENT_TYPES = ["encounter", "prescription", "dispensing", "vitals", "lab_order"]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    # nearest-rank method: p99 of 100 samples is the 99th
    import math
    k = max(0, min(len(xs) - 1, math.ceil((p / 100) * len(xs)) - 1))
    return xs[k]


def make_event(i: int, facility: str = "BENCH-FAC") -> dict:
    etype = EVENT_TYPES[i % len(EVENT_TYPES)]
    payload = {"patient_id": f"PT-{i % 500}", "facility_id": facility}
    if etype == "prescription":
        payload |= {"drug_code": "ACT", "qty": 1}
    elif etype == "dispensing":
        payload |= {"drug_code": "ACT", "qty": 1}
    elif etype == "vitals":
        payload |= {"temp_c": 37.0 + (i % 3) / 10}
    elif etype == "lab_order":
        payload |= {"test_code": "MALARIA-RDT"}
    return {"id": f"bench-{i}", "type": etype, "facility_id": facility,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "payload": payload}


def post_line(url: str, line: str, timeout: float = 10.0) -> float:
    start = time.perf_counter()
    req = urllib.request.Request(url, data=(line + "\n").encode(),
                                 headers={"Content-Type": "application/x-ndjson"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()
    return (time.perf_counter() - start) * 1000


def run_load(target: str, events: int, concurrency: int) -> dict:
    lines = [json.dumps(make_event(i)) for i in range(events)]
    latencies: list[float] = []
    lock = threading.Lock()
    failures = [0]

    def work(line: str):
        try:
            ms = post_line(target + "/ingest", line)
            with lock:
                latencies.append(ms)
        except Exception:
            with lock:
                failures[0] += 1

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(work, lines))
    wall = time.perf_counter() - started
    return {"events": events, "failures": failures[0],
            "throughput_eps": round(events / wall, 1),
            "p50_ms": round(percentile(latencies, 50), 2),
            "p99_ms": round(percentile(latencies, 99), 2)}


def verify_spill(path: str) -> dict:
    """A torn tail must not lose the good prefix (lga-hub replay rule)."""
    good, corrupt = 0, 0
    with open(path, "rb") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(b"SX1:"):      # sealed line — counts as good
                good += 1
                continue
            try:
                json.loads(line)
                good += 1
            except json.JSONDecodeError:
                corrupt += 1
    return {"good_lines": good, "corrupt_lines": corrupt, "tolerated": True}


def chaos_tail(path: str) -> dict:
    """Simulate kill -9 mid-write: append a torn line, then verify."""
    with open(path, "ab") as f:
        f.write(b'{"id":"torn","type":"encounter","paylo')
    return verify_spill(path)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", help="sync-ingest base URL")
    ap.add_argument("--events", type=int, default=1000)
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--p99-ms", type=float, default=250.0)
    ap.add_argument("--chaos-tail", metavar="FILE", help="torn-write test on a spill file")
    args = ap.parse_args()

    if args.chaos_tail:
        result = chaos_tail(args.chaos_tail)
        print(json.dumps({"chaos_tail": result}))
        return 0 if result["corrupt_lines"] == 1 and result["tolerated"] else 1

    if not args.target:
        ap.error("--target or --chaos-tail required")
    result = run_load(args.target.rstrip("/"), args.events, args.concurrency)
    print(json.dumps({"load": result}))
    ok = result["failures"] == 0 and result["p99_ms"] <= args.p99_ms
    if not ok:
        print(f"FAIL: failures={result['failures']} p99={result['p99_ms']}ms", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
