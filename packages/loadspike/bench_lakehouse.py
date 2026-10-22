"""bench_lakehouse.py — silver/gold build throughput at facility scale.

Feeds N synthetic events through the real lakehouse.build() and measures
wall time + events/sec. Correctness gates assert the medallion counts
are exact — timing is reported, never asserted (hardware varies).
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "lakehouse"))
import lakehouse  # noqa: E402


def synth_events(n, n_facilities=500):  # one LGA hub's catchment
    out = []
    for i in range(n):
        out.append({
            "type": ["encounter_held", "claim_filed", "stock_counted"][i % 3],
            "facility_id": "OHF-%06d" % (i % n_facilities),
            "patient_ref": "pseudo-%08d" % (i % 50000),
            "at": 1761200000 + i,
            "payload": {"k": i % 10},
        })
    return out


def bench(n=100_000):
    events = synth_events(n)
    with tempfile.TemporaryDirectory() as d:
        t0 = time.perf_counter()
        result = lakehouse.build(events, d)
        build_s = time.perf_counter() - t0
        # correctness gate: every event accounted for in silver
        silver_counts = {}
        silver_dir = os.path.join(d, "silver")
        for part in os.listdir(silver_dir):
            with open(os.path.join(silver_dir, part, "records.jsonl")) as f:
                silver_counts[part] = sum(1 for _ in f)
        assert sum(silver_counts.values()) == n, \
            "silver lost events: %s" % silver_counts
    return {
        "events": n,
        "build_s": round(build_s, 3),
        "build_eps": round(n / build_s),
        "silver_partitions": silver_counts,
        "result_keys": sorted(result.keys()) if hasattr(result, "keys") else str(type(result)),
    }


if __name__ == "__main__":
    print(json.dumps({"bench": "lakehouse", **bench()}, indent=2))
