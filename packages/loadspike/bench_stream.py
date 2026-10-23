"""bench_stream.py — throughput harness for the ADR-0066 stream contract.

Measures the reference engine under synthetic national-scale load:
produce rate, consume rate, dedupe overhead, replay cost. Numbers are
RECORDED, not asserted (hardware varies); correctness invariants are
asserted — a fast wrong answer fails, a slow right answer reports.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "streamspike"))
from refstream import RefTopic, RefConsumer, per_key_order_ok  # noqa: E402

N_FACILITIES = 8800  # PHC-scale: one T1 hub's worst case


def synth_events(n, n_facilities=N_FACILITIES):
    seqs = {}
    out = []
    for i in range(n):
        f = "OHF-%06d" % (i % n_facilities)
        seqs[f] = seqs.get(f, 0) + 1
        out.append({"event_id": "ev-%08d" % i, "facility_id": f,
                    "seq": seqs[f], "event_type": "encounter_held",
                    "at": 1761200000 + i})
    return out


def bench(n=200_000):
    events = synth_events(n)
    t = RefTopic("bench")

    t0 = time.perf_counter()
    for ev in events:
        t.produce(ev["facility_id"], ev)
    produce_s = time.perf_counter() - t0

    c = RefConsumer(t)
    t0 = time.perf_counter()
    got = c.poll()
    consume_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    dup = c.replay_from(0)  # full replay: dedupe must swallow all
    replay_s = time.perf_counter() - t0

    # correctness gates — these DO assert
    assert len(got) == n, "consume lost events"
    assert dup == [], "dedupe failed on replay"
    assert per_key_order_ok(t), "per-key ordering violated"

    return {
        "events": n,
        "facilities": N_FACILITIES,
        "produce_eps": round(n / produce_s),
        "consume_eps": round(n / consume_s),
        "full_replay_s": round(replay_s, 3),
        "dedupe_overhead_eps": round(n / replay_s),
    }


if __name__ == "__main__":
    print(json.dumps({"bench": "stream", **bench()}, indent=2))
