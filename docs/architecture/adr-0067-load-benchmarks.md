# ADR-0067: Load benchmarks — from "designed to scale" to measured

## Status
Adopted as a living harness. Numbers re-measured on every perf-relevant
change; `run_load.sh` ships for real-hardware runs.

## Context
The 200M-citizen claim rests on federation (no node faces national
volume), but federation claims need measurements, not adjectives.
`packages/loadspike` measures the three hot paths with correctness
gates asserted and timings recorded (never asserted — hardware varies).

## Baseline numbers (sandbox Xeon, single core, 2026-10-22)
| Path | Measured | National-scale reading |
|---|---|---|
| Stream contract produce | **1.21M events/s** | A state hub's entire daily event volume (~5M citizens × a few events) ingests in seconds |
| Stream consume + dedupe | **1.80M / 3.19M eps** | Consumer lag is never the bottleneck at contract level |
| Lakehouse bronze→silver→gold | **86K events/s** | 100M-record national bronze rebuild ≈ 20 min single-core; parallel by partition in production |
| Mesh hop (chain-MAC + verify + persist) | **19.3K hops/s** | ~1.7M mesh messages/hub/day sustained — 20× headroom over a busy LGA |
| Mesh drain after partition | **21.9K entries/s** | A 6-hour outage's backlog at a busy PHC (~10K entries) flushes in < 1 s |
| Mesh over the wire, TLS keep-alive | **6.2K hops/s** | Real TCP+TLS on loopback costs ~3× vs in-process; still 530M messages/hub/day |
| Mesh over the wire, handshake per hop | **382 hops/s** | **16× penalty — keep-alive is a REQUIRED deployment setting, recorded as such** |

## Honest caveats (recorded, not hidden)
- Reference-engine numbers (stream) model the CONTRACT, not a running
  Fluvio cluster; live cluster numbers land with ADR-0066 live verify.
- Wire numbers are loopback TLS: real handshakes and HTTP framing, but
  WAN latency/loss are NOT simulated — that needs the chaos harness.
  The keep-alive finding (6.2K vs 382 hops/s) is protocol cost, not
  latency, so it transfers to WAN conditions.
- Lakehouse numbers are single-threaded Python; production parallelises
  by partition trivially.
- NOT measured yet: NIN-binding service shard throughput, WhatsApp/telco
  channel ceilings (theirs, not ours), concurrent-tenant isolation cost.

## Consequences
- Perf regressions become detectable in CI (correctness gates) and on
  hardware (run_load.sh diffs metrics.json).
- Deferred: live-cluster stream numbers, wire-level mesh benchmark with
  TLS, chaos-under-load for the fail-closed paths.
