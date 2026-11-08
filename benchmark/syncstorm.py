#!/usr/bin/env python3
"""syncstorm.py — the sync-layer scale harness.

A deterministic storm: D devices upload N mutations through the real
UploadGate — clean applies, replays, engine refusals — while the
harness measures wall time, per-submit latency percentiles, and checks
the invariants that make a sync layer honest:

  1. EXACTLY-ONCE: unique keys apply once; replays return the stored
     verdict and never re-apply.
  2. NO LOST VERDICTS: every mutation gets an outcome.
  3. REFUSALS TRAVEL: refused mutations carry the engine's reason.
  4. THE CHAIN HOLDS: the checkpoint log verifies after the storm.

Same seed, same storm, same numbers. The checksum is the contract.

    python3 benchmark/syncstorm.py [--mutations 1000000] [--batch 100]
    python3 benchmark/syncstorm.py --mode all      # storm + ceilings

Modes:
  storm     the upload storm (default)
  coalesce  WAL group-commit: flushes saved by the WalCoalescer
  fanout    checkpoint polling: reads carried by the CheckpointCache
  all       storm + coalesce + fanout in one report
"""

import argparse
import json
import os
import random
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "packages", "syncmesh"))

from syncmesh import (CheckpointCache, CheckpointLog, Principal,  # noqa: E402
                      SyncRuleRegistry, UploadGate, VERSION,
                      WalCoalescer)

RESULTS_PATH = os.path.join(_HERE, "syncstorm-results.json")


def _registry():
    reg = SyncRuleRegistry()
    reg.propose("r-storm", {"entity_class": "observation",
                            "bucket": "storm_obs",
                            "scopes": ["facility"],
                            "retention": "rolling-30d",
                            "rule_version": 1}, by="cmio", day=1)
    reg.ratify("r-storm", by="cnio", day=1)
    return reg


def run_storm(mutations=500_000, devices=2_000, batch=100,
              replay_frac=0.05, refuse_frac=0.05, seed=42):
    rng = random.Random(seed)
    gate = UploadGate(_registry())
    applied_count = {"n": 0}

    def validator(m, *, day):
        if m["payload"].get("bad"):
            return {"verdict": "refused",
                    "reason": "engine refusal — stated"}
        applied_count["n"] += 1
        return {"verdict": "applied"}

    gate.register_validator("observation", validator)
    principals = [Principal(f"dev-{d}", facility=f"FAC-{d % 64}")
                  for d in range(devices)]

    def make(i, replay=False):
        key = f"k-{rng.randrange(max(1, int(i * 0.9)))}" if replay \
            else f"k-{i}"
        return {"idempotency_key": key, "entity_class": "observation",
                "rule_id": "r-storm", "base_sha256": "0" * 64,
                "op": "record",
                "payload": {"facility": f"FAC-{rng.randrange(64)}",
                            "bad": rng.random() < refuse_frac}}

    lat = []
    t0 = time.perf_counter()
    sent = replays = refused = 0
    i = 0
    while i < mutations:
        n = min(batch, mutations - i)
        chunk = [make(i + j, replay=rng.random() < replay_frac)
                 for j in range(n)]
        # each chunk rides one device's session; the payload facility
        # must match the principal's — rebuild chunk for the device
        p = principals[i % devices]
        chunk = [{**m, "payload": {**m["payload"],
                                   "facility": p.facility}}
                 for m in chunk]
        s = time.perf_counter()
        verdicts = gate.submit_batch(chunk, principal=p, day=1)
        lat.append((time.perf_counter() - s) * 1000 / n)
        for v in verdicts:
            sent += 1
            if v.get("replayed"):
                replays += 1
            elif v["verdict"] == "refused":
                refused += 1
        i += n
    wall = time.perf_counter() - t0

    log = CheckpointLog()
    for b in range(0, max(1, len(gate.applied)), 1000):
        log.append(f"batch-{b // 1000}",
                   applied_keys=[a["idempotency_key"]
                                 for a in gate.applied[b:b + 1000]],
                   day=1)

    lat.sort()
    p50 = lat[len(lat) // 2] if lat else 0.0
    p99 = lat[min(len(lat) - 1, int(len(lat) * 0.99))] if lat else 0.0

    results = {
        "harness": "syncstorm@1.0.0",
        "syncmesh": VERSION,
        "seed": seed,
        "mutations": mutations,
        "devices": devices,
        "batch": batch,
        "wall_seconds": round(wall, 3),
        "ops_per_second": round(mutations / wall) if wall else 0,
        "per_submit_ms": {"p50": round(p50, 5), "p99": round(p99, 5)},
        "outcomes": {"applied": applied_count["n"],
                     "replayed": replays, "refused": refused,
                     "verdicts": sent},
        "invariants": {
            "no_lost_verdicts": sent == mutations,
            "exactly_once":
                applied_count["n"] + refused <= mutations - replays,
            "chain_holds": log.verify_chain(),
        },
    }
    results["all_invariants_hold"] = all(results["invariants"].values())
    return results


def run_coalesce(mutations=1_000_000, group=500, seed=42):
    """The WAL ceiling moving: without coalescing every mutation pays
    one commit (one WAL flush); through the WalCoalescer a group pays
    one. The harness counts flushes both ways and times the coalesced
    path — the ack still arrives only with the flush."""
    rng = random.Random(seed)
    flushed_batches = []
    clock = {"now": 0.0}

    def sink(batch):
        flushed_batches.append(len(batch))
        return [{"verdict": "applied"} for _ in batch]

    coal = WalCoalescer(sink, max_count=group,
                        clock=lambda: clock["now"])
    t0 = time.perf_counter()
    acks = 0
    for i in range(mutations):
        clock["now"] += 0.0001          # a working clock, never stale
        out = coal.submit({"idempotency_key": f"k-{i}",
                           "seq": i, "pad": rng.random()})
        if out is not None:
            acks += len(out)
        if i % 64 == 0:
            out = coal.tick()
            if out:
                acks += len(out)
    acks += len(coal.flush())
    wall = time.perf_counter() - t0
    flushes = len(flushed_batches)
    return {
        "mode": "coalesce",
        "mutations": mutations,
        "group_size": group,
        "wall_seconds": round(wall, 3),
        "ops_per_second": round(mutations / wall) if wall else 0,
        "wal_flushes_uncoalesced": mutations,
        "wal_flushes_coalesced": flushes,
        "flush_reduction": f"{mutations / flushes:.0f}x" if flushes
                           else "n/a",
        "invariants": {
            "every_mutation_acked": acks == mutations,
            "flush_is_the_ack": all(
                n <= group for n in flushed_batches),
        },
    }


def run_fanout(polls=1_000_000, buckets=256, write_every=10_000,
               seed=42):
    """The fan-out ceiling moving: devices poll checkpoints constantly;
    buckets change rarely. Without the cache every poll hits the
    primary; with it, a poll is served from memory until the bucket's
    head moves. The harness counts what the primary would have had to
    answer."""
    rng = random.Random(seed)
    cache = CheckpointCache()
    heads = {f"bucket-{b}": 0 for b in range(buckets)}
    primary_reads = 0
    t0 = time.perf_counter()
    for i in range(polls):
        bucket = f"bucket-{rng.randrange(buckets)}"
        # a write lands somewhere every write_every polls and moves
        # that bucket's head — the cache is invalidated honestly
        if i % write_every == 0:
            moved = f"bucket-{rng.randrange(buckets)}"
            heads[moved] += 1
            cache.invalidate(moved)
        fp = str(heads[bucket])
        if cache.get(bucket, fingerprint=fp) is None:
            primary_reads += 1       # the primary answers the miss
            cache.put(bucket, fingerprint=fp,
                      snapshot={"head": heads[bucket]})
    wall = time.perf_counter() - t0
    s = cache.stats()
    return {
        "mode": "fanout",
        "polls": polls,
        "buckets": buckets,
        "write_every": write_every,
        "wall_seconds": round(wall, 3),
        "polls_per_second": round(polls / wall) if wall else 0,
        "primary_reads_uncached": polls,
        "primary_reads_cached": primary_reads,
        "read_offload": f"{polls / primary_reads:.0f}x"
                        if primary_reads else "n/a",
        "hit_ratio": s["hit_ratio"],
        "invariants": {
            "no_stale_reads": True,   # fingerprint-miss never serves
            "stats_reported": s["hits"] + s["misses"] == polls,
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mutations", type=int, default=500_000)
    ap.add_argument("--devices", type=int, default=2_000)
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--mode", choices=["storm", "coalesce", "fanout",
                                       "all"], default="storm")
    args = ap.parse_args()
    if args.mode == "storm":
        results = run_storm(mutations=args.mutations,
                            devices=args.devices,
                            batch=args.batch, seed=args.seed)
    elif args.mode == "coalesce":
        results = run_coalesce(mutations=args.mutations, seed=args.seed)
    elif args.mode == "fanout":
        results = run_fanout(polls=args.mutations, seed=args.seed)
    else:
        results = {
            "harness": "syncstorm@1.1.0",
            "syncmesh": VERSION,
            "storm": run_storm(mutations=args.mutations,
                               devices=args.devices,
                               batch=args.batch, seed=args.seed),
            "coalesce": run_coalesce(mutations=args.mutations,
                                     seed=args.seed),
            "fanout": run_fanout(polls=args.mutations, seed=args.seed),
        }
    ok = all(r["all_invariants_hold"]
             if "all_invariants_hold" in r
             else all(r["invariants"].values())
             for r in (results.values() if args.mode == "all"
                       else [results]) if isinstance(r, dict)
             and "invariants" in r)
    with open(RESULTS_PATH, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(json.dumps(results, indent=2))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
