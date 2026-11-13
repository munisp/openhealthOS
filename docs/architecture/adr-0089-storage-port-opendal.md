# ADR-0089: One storage port — Apache OpenDAL as the main storage engine

- Status: accepted
- Date: 2026-11-13
- Wave: 25 (README row 230)
- Amends: ADR-0005 (scoped — see Decision 2)

## Context

The platform stores objects — imaging, lab PDFs, discharge summaries,
insurance documents, media attachments, archives — and until now had
no port for them: the estate's writes are local JSON payloads through
payloadio (ADR-0088), which owns *local payload atomicity* and nothing
else. Every deployment target this platform serves — a ministry server
room, a clinic process on a laptop, a cloud archive — needs the same
document operations over different backends, on networks where a
transfer interrupted at 90% must resume at 90%, not restart from zero.

Apache OpenDAL (Apache-2.0) provides one `Operator` API over 50+
storage services with retry, timeout, and concurrency as composable
layers — backends selected by configuration, not code branches. That
is the country/company-agnostic doctrine applied to storage.

## Decision

1. **`packages/storageport` is the one storage port.** All object
   storage flows through `storageport.connect(name)` against a
   registry entry. Deployment targets are DATA (`REGISTRY`), never
   code branches.

2. **OpenDAL is the MAIN engine — not an option.** When the opendal
   wheel is importable, *every* scheme — fs and memory included — runs
   through opendal, layered with `RetryLayer(max_times=3)` (retry is a
   layer; the policy is data). ADR-0005's stdlib-only rule is amended
   in exactly one scoped place: storageport holds a runtime dependency
   on the opendal wheel. Every other package remains stdlib-only. The
   suite pins `ADAPTER == "opendal"`: if the main engine is absent,
   the gate goes red and names it.

3. **The stdlib fallback is declared degraded mode, not a second
   engine.** It serves fs and memory only, refuses cloud schemes by
   name, and stamps `adapter` on every receipt. Fail-closed doctrine:
   degraded service with the mode declared beats a dead service — but
   nothing silently falls back to anywhere.

4. **Refusals are stated, pre-network.** An unknown entry, a missing
   required secret, an unsupported capability, or a capability the
   Python binding does not expose (presign: core supports it on s3,
   the binding ships no `presign_*` methods) is a `StorageRefusal`
   with the reason named — never a signing error from the wire, never
   a silent None. Absent objects are typed `AbsentKey`: absence is not
   a value. Credentials are never values in the shipped registry; they
   arrive via `secrets=` at connect time.

5. **The checksum is the contract.** `put` returns a receipt carrying
   size and sha256; `checksum(key)` re-proves stored bytes. The demo's
   64 KiB source is a deterministic sha256 stream so the roundtrip is
   provable, not plausible.

6. **Layer boundary stated both directions.** Object stores are atomic
   on PUT; payloadio owns tmp+rename atomicity for local JSON
   payloads. storageport stores documents and media; payloadio lands
   the proof payloads. Neither replaces the other.

## Consequences

- Six deterministic drills (storageproof-demo@1.0.0): faithful
  roundtrip through the opendal fs service, range-read resume, typed
  absence, two stated refusals (capability, credentials — both
  pre-network), degraded mode declared (amber), and the live adapter
  census. Surfaced at `apps/pwa/storageproof.html`, cached by
  vhc-shell-v24, registered in the proof index, the refresh rail, and
  the meta gate in this wave.
- Cloud backends (s3) are exercisable the moment secrets are injected
  — the registry entry ships with `requires` declared, so an
  unconfigured cloud is a stated refusal today and a configuration
  tomorrow.
- Upgrades of the opendal wheel are gated work: the binding is
  pre-1.0 and introduces breaking changes between releases; the
  version is pinned in the toolchain and recorded in the census.

## Alternatives considered

- **Optional adapter behind a stdlib-first port** — rejected by
  direction: the storage engine must be uniform, not bifurcated; two
  engines with equal standing is two sets of behaviour to audit.
- **Raw boto3/filesystem per surface** — rejected: every backend
  becomes a code branch, the exact anti-pattern the registry doctrine
  exists to prevent.
- **Extend payloadio to objects** — rejected: payloadio's contract is
  local atomic rename for proof payloads; conflating layers blurs both
  contracts.
