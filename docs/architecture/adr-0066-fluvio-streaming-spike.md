# ADR-0066: Fluvio as the edge streaming backbone (spike)

## Status
Spike (honest): producer/consumer contract + reference engine pinned by
goldens; live cluster verify pending a Docker/Fluvio-capable runner.

## Context
The lakefeed today is batch-oriented: facilities write event files,
bronze ingests on a schedule. Two pressures argue for a streaming
backbone at the T1 (LGA/state hub) tier:

1. **Surveillance latency** — outbreak signals (ADR: surveillance
   package) lose value in daily batches; a measles cluster wants
   minutes, not tomorrow.
2. **Disconnected operation** — facilities go dark for hours. Whatever
   carries events must buffer at the edge and sync on reconnect without
   operator action.

Kafka is the default answer and the wrong one here: JVM footprint,
ZooKeeper/KRaft operational burden, no ARM64 edge story. Fluvio is
Apache-2.0 (licence gate: PASS — verified against
github.com/fluvio-community/fluvio), a ~37MB Rust single binary, runs
on ARM64/Raspberry-Pi-class hardware, with built-in caching and
offline mirroring designed for exactly our link profile. Its commercial
tier (InfinyOn Cloud, SDF single-use licence) is a runtime service we
do not embed — the open-source cluster is the component under test.

## Placement
T1 hub and above only. T0 (phone/CHW) speaks the existing
store-and-forward mesh; the mesh drains INTO Fluvio at the first
connected hub. Fluvio never replaces the mesh — they sit in different
layers, as with MQTT in the Home Assistant bridge (howUdeyChat ADR
notes): brokers own the last connected hop, the mesh owns the wild.

## Topic contract (packages/streamspike/topics.json)
One topic per event family, single-partition at T1 scale, keys are
facility ids for ordering affinity:

- `lakefeed.clinical`   — encounter/observation events
- `lakefeed.claims`     — NHIA claim events (feeds graphspike)
- `lakefeed.stock`      — stocktake/coldchain events
- `lakefeed.adherence`  — howUdeyChat adherence snapshots (pseudonymous)
- `surveillance.signal` — syndromic signals, highest priority consumer

## Spike outcome
- `spike.py` — stdlib reference engine implementing the contract's
  invariants: per-key ordering, at-least-once with consumer-side dedupe
  (event ids), and replay-from-offset after consumer restart.
- Golden-pinned against a captured fixture stream (9/9 tests green).
- `run_fluvio.sh` — live path: installs the Fluvio CLI, starts a local
  cluster, creates the contract topics, produces the fixture, consumes
  and diffs against goldens. Exits 2 honestly where no cluster runtime
  exists (this sandbox).

## Consequences
- If adopted: T1 hubs run a Fluvio cluster; lakehouse bronze gains a
  streaming consumer path alongside batch; surveillance latency drops
  to consumer lag; the mesh unchanged.
- Deferred: live cluster verify, SmartModule (WASM) inline transforms
  (e.g. free-text scrub at ingest — natural home for the mednlp
  scrubber), geo-mirroring topology to the national tier.
