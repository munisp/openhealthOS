#!/usr/bin/env python3
"""edge_latency — HTTP latency percentile harness (stdlib, no deps).

Evidence-first performance work: before anyone touches wire formats or
RPC stacks, this harness answers "where does the time actually go" for
the platform's many-small-API-calls workload. It hammers a target URL
with N requests at C concurrency and reports p50/p95/p99, throughput,
and per-request connect/TTFB splits — the numbers that decide whether
HTTP/3, caching, or serialization is the real lever.

Usage:
  python3 edge_latency.py --url http://edge:8080/api/healthz \
      --requests 500 --concurrency 10 [--warmup 20] [--json out.json]

Loss simulation (run on the edge node, as root, BEFORE the bench):
  tc qdisc add dev eth0 root netem loss 2% delay 80ms 20ms
  # ... run the bench ...
  tc qdisc del dev eth0 root
The --loss-profile flag just prints these commands for common profiles;
actual impairment needs tc/netem on the path, which a client-side script
cannot do for you.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import http.client
import json
import ssl
import statistics
import sys
import time
import urllib.parse
from dataclasses import dataclass

LOSS_PROFILES = {
    "lan":      ("eth0", "loss 0% delay 1ms"),
    "wifi-ward":("wlan0", "loss 1% delay 30ms 10ms"),
    "3g":       ("eth0", "loss 2% delay 80ms 20ms rate 2mbit"),
    "2g-edge":  ("eth0", "loss 5% delay 300ms 100ms rate 250kbit"),
}


@dataclass
class Sample:
    ok: bool
    total_ms: float
    connect_ms: float
    ttfb_ms: float
    status: int
    bytes_in: int
    error: str = ""


def one_request(parsed: urllib.parse.ParseResult, timeout: float) -> Sample:
    """One measured GET. Connect and TTFB are split so you can see
    handshake cost (where QUIC helps) vs server think time (where it
    doesn't)."""
    use_tls = parsed.scheme == "https"
    port = parsed.port or (443 if use_tls else 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    t0 = time.perf_counter()
    try:
        if use_tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False  # edge nodes use Caddy's internal CA
            ctx.verify_mode = ssl.CERT_NONE
            conn = http.client.HTTPSConnection(parsed.hostname, port, timeout=timeout, context=ctx)
        else:
            conn = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
        conn.connect()
        t_conn = time.perf_counter()
        conn.request("GET", path, headers={"Connection": "close", "Accept": "application/json"})
        resp = conn.getresponse()
        t_ttfb = time.perf_counter()
        body = resp.read()
        t_end = time.perf_counter()
        conn.close()
        return Sample(resp.status < 400, (t_end - t0) * 1000,
                      (t_conn - t0) * 1000, (t_ttfb - t0) * 1000,
                      resp.status, len(body))
    except Exception as e:  # noqa: BLE001 — a benchmark reports, never hides
        return Sample(False, (time.perf_counter() - t0) * 1000, 0, 0, 0, 0, str(e))


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def summarize(samples: list[Sample], wall_s: float) -> dict:
    ok = [s for s in samples if s.ok]
    bad = [s for s in samples if not s.ok]
    lat = sorted(s.total_ms for s in ok)
    conn = sorted((s.connect_ms for s in ok))
    ttfb = sorted((s.ttfb_ms for s in ok))
    def stats(v: list[float]) -> dict:
        return {
            "p50": round(percentile(v, 50), 2),
            "p95": round(percentile(v, 95), 2),
            "p99": round(percentile(v, 99), 2),
            "mean": round(statistics.fmean(v), 2) if v else 0.0,
        }
    errors: dict[str, int] = {}
    for s in bad:
        errors[s.error or f"http-{s.status}"] = errors.get(s.error or f"http-{s.status}", 0) + 1
    return {
        "requests": len(samples), "ok": len(ok), "failed": len(bad),
        "wall_s": round(wall_s, 2),
        "rps": round(len(samples) / wall_s, 1) if wall_s else 0.0,
        "total_ms": stats(lat), "connect_ms": stats(conn), "ttfb_ms": stats(ttfb),
        "bytes_total": sum(s.bytes_in for s in ok),
        "errors": errors,
    }


def run(url: str, requests: int, concurrency: int, warmup: int, timeout: float) -> dict:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be http(s)://")
    for _ in range(warmup):
        one_request(parsed, timeout)
    t0 = time.perf_counter()
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        samples = list(pool.map(lambda _: one_request(parsed, timeout), range(requests)))
    wall = time.perf_counter() - t0
    r = summarize(samples, wall)
    r["target"] = url
    r["concurrency"] = concurrency
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="edge_latency")
    ap.add_argument("--url", help="target URL, e.g. http://edge:8080/api/healthz")
    ap.add_argument("--requests", type=int, default=500)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--warmup", type=int, default=20)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--json", default="", help="write full report to this path")
    ap.add_argument("--loss-profile", choices=sorted(LOSS_PROFILES),
                    help="print tc/netem commands for a profile and exit")
    args = ap.parse_args(argv)

    if args.loss_profile:
        dev, spec = LOSS_PROFILES[args.loss_profile]
        print(f"# {args.loss_profile} — run on the node, as root:")
        print(f"sudo tc qdisc add dev {dev} root netem {spec}")
        print(f"# ... run the benchmark ...")
        print(f"sudo tc qdisc del dev {dev} root")
        return 0
    if not args.url:
        ap.error("--url is required (or use --loss-profile)")

    report = run(args.url, args.requests, args.concurrency, args.warmup, args.timeout)
    json.dump(report, sys.stdout, indent=2)
    print()
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
    return 0 if report["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
