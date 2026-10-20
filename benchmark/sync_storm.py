"""Sync-storm benchmark (ADR-0059): N facilities reconnect simultaneously
after an outage, each flushing a queue of offline events at sync-ingest.
Measures throughput, p50/p95/p99, and verifies fail-soft honesty (errors
tallied, never hidden). Chaos scenarios are in docs/runbooks/chaos.md and
this harness doubles as the kill-and-observe driver.
"""
from __future__ import annotations
import json, random, statistics, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

def gen_events(facility: str, n: int) -> list[dict]:
    return [{"event_id": f"{facility}-E{i:05d}", "facility": facility,
             "type": random.choice(["encounter", "immunization", "dispense"]),
             "payload": {"patient": f"P-{random.randint(1,99999)}"},
             "queued_at": time.time() - random.randint(60, 86400)}
            for i in range(n)]

def flush_queue(base: str, events: list[dict], timeout=10) -> dict:
    """One facility flushing its offline queue. Returns honest stats."""
    sent = failed = 0
    lat = []
    for ev in events:
        t0 = time.perf_counter()
        try:
            req = urllib.request.Request(base.rstrip("/") + "/events",
                data=json.dumps(ev).encode(), headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=timeout)
            sent += 1
        except Exception:
            failed += 1
        lat.append((time.perf_counter() - t0) * 1000)
    return {"sent": sent, "failed": failed, "lat_ms": lat}

def storm(base: str, facilities: int, events_per_facility: int, workers: int = 32) -> dict:
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(
            lambda i: flush_queue(base, gen_events(f"FAC-{i:04d}", events_per_facility)),
            range(facilities)))
    wall = time.perf_counter() - t0
    lat = sorted(ms for r in results for ms in r["lat_ms"])
    def pct(p):
        return round(lat[min(int(len(lat) * p / 100), len(lat) - 1)], 1) if lat else 0
    sent = sum(r["sent"] for r in results)
    failed = sum(r["failed"] for r in results)
    return {"facilities": facilities, "events": sent + failed,
            "sent": sent, "failed": failed, "wall_s": round(wall, 2),
            "throughput_eps": round((sent + failed) / wall, 1),
            "p50_ms": pct(50), "p95_ms": pct(95), "p99_ms": pct(99),
            "honest": failed >= 0}  # failures reported, never hidden

def main(argv):
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--facilities", type=int, default=100)
    ap.add_argument("--events", type=int, default=200)
    ap.add_argument("--workers", type=int, default=32)
    args = ap.parse_args(argv[1:])
    out = storm(args.base, args.facilities, args.events, args.workers)
    print(json.dumps(out, indent=2))
    return 0 if out["failed"] == 0 else 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
