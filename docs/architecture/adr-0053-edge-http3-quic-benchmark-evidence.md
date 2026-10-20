# ADR-0053: HTTP/3 (QUIC) at the Caddy edge + latency benchmark harness — and why not gRPC

Status: accepted (2026-10-20)

## Context

A question arose: would gRPC improve platform performance? Analysis said
**no** for the workloads we actually have, for structural reasons:

1. **Our bottleneck is the edge, not the datacenter.** Facilities run on
   2G/3G and congested ward Wi-Fi. gRPC's headline win — HTTP/2
   multiplexing — *collapses* over high-loss links because TCP-level
   head-of-line blocking stalls all multiplexed streams behind one lost
   packet. QUIC exists precisely to fix this.
2. **Browsers can't speak gRPC natively.** The PWA and dashboards would
   need grpc-web plus an Envoy translation layer — a new component and a
   new hop for zero edge benefit.
3. **Stdlib-only Go doctrine.** gRPC-Go drags in protobuf, x/net, and a
   protoc codegen toolchain into 27 services that currently build with
   zero dependencies. That doctrine is why builds never fail on a flaky
   goproxy link and why any Go installation anywhere compiles the
   platform.
4. **Our payload profile doesn't match.** Traffic is small, infrequent,
   and deliberately offline-queued (store-and-forward beats live streams
   when the network drops every 20 minutes). The big payloads — DICOM,
   video, documents — are bulk binary, where RPC framing adds nothing.

## Decision

**Reject gRPC.** Adopt the two moves that attack the real bottleneck:

### 1. HTTP/3 (QUIC) at the Caddy edge (Caddyfile + compose)

`servers :443 { protocols h1 h2 h3 }` plus `443:443/udp` in
`infra/compose/edge.yaml`. QUIC removes TCP head-of-line blocking
(independent streams) and gives 0-RTT resumption — directly targeting
the lossy-link latency our clients live with. Caddy already ships QUIC
in the same static binary; **no new dependency, no code change, no
contract change** — h1/h2 clients fall back transparently. This is the
rare performance lever that costs us nothing in doctrine.

### 2. `benchmark/edge_latency.py` — evidence-first harness

Before any future wire-format or RPC discussion, this stdlib harness
measures what the platform actually experiences: p50/p95/p99 with
**connect/TTFB phase splits** (so handshake cost — where QUIC helps —
is visible separately from server think time — where it doesn't),
throughput, and honest error tallies. `--loss-profile` prints ready
tc/netem commands for four profiles (lan, wifi-ward, 3g, 2g-edge) so
benchmarks run under *simulated Nigerian last-mile conditions*, not a
datacenter loopback fantasy. Exit code is non-zero on any failure — a
benchmark that hides errors manufactures confidence.

### If a datacenter hot path ever proves itself

If profiling one day shows JSON serialization as a *measured* hotspot
(e.g. claims-orchestrator ↔ adjudicator), the escape hatch is
**Connect-RPC** on that one path — protobuf that also speaks plain
HTTP+JSON, browser-friendly, composes with net/http, no Envoy. That
decision requires numbers from this harness first.

## Consequences

- Edge latency under packet loss improves with zero code churn; PWA
  gets it free (browsers negotiate HTTP/3 automatically).
- Every future performance claim must arrive with edge_latency numbers
  under a named loss profile — evidence-first is now the standing rule.
- gRPC is recorded as rejected with reasoning; revisiting requires
  measured proof of a datacenter serialization hotspot.
- Limitation: HTTP/3 is edge-ingress only (client→Caddy). Upstream
  Caddy→services stays h1 on the LAN, where loss is ~zero and QUIC
  adds nothing.
