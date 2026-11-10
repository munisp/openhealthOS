# ADR-0072 — Knowledge-graph seam: semantics as data, federation fail-closed

- Status: accepted
- Date: 2026-11-11
- Wave: 11 (README row 210)

## Context

The estate binds reports to codes (packages/terminology — versioned
deployment maps, SNOMED licence-gated) and pins payloads, models, and
runs (proofindex, aigov, inferlock). What it never had is the semantic
layer between codes and data: concept hierarchies with computable
subsumption, cross-system mappings, shape validation, and a seam for
federated knowledge bases.

External RDF/SPARQL platforms — OxiRS (Apache-2.0, pure Rust),
Apache Jena — solve this at engine scale. The question was whether the
platform embeds one. It does not:

1. ADR-0005 — packages are stdlib-only Python; a Rust or JVM engine
   cannot be embedded into the package estate.
2. Licence doctrine is satisfied (Apache-2.0 is embed-clean), but
   embedding is refused on architectural grounds, not licensing.
3. What the estate actually needs is the SEAM: graphs, mappings,
   shapes, and endpoints as governed data — with external engines
   composing exactly like notary backends (ledgeranchor, ADR-0069) and
   PSP export adapters (vhpsp): ratified templates + injected
   transports.

## Decision

1. **Concepts are registry entries, never code branches.** A concept
   that is not registered cannot be subsumed, mapped, or validated
   against — absence is stated, never faked.
2. **The hierarchy is a DAG by construction.** Parents must be
   registered before children; self-parents and ghost parents are
   refused at write time. Cycles are not detected — they are
   unconstructable. Subsumption closures are computed against the
   pinned graph version; any mutation invalidates the memo.
3. **Mappings are claims, and claims need four eyes.** Fewer than two
   distinct named approvers: the mapping exists but refuses to
   traverse. The kind vocabulary is closed (exact / narrower /
   broader / related) so imperfect equivalence cannot masquerade as
   exact.
4. **Shapes carry a closed constraint vocabulary** (required /
   datatype / max_count / in / closed). Unknown kinds are refused at
   registration — validation never guesses. Violations are named with
   kind + path. `bool` is not `int`.
5. **Federation fails closed.** Endpoints are https-only and require
   two distinct approvers before any query runs. Template adapters
   (oxirs-fuseki, jena-fuseki) refuse until ratified. The transport is
   injected; a missing wire or a wire error is stated NOT CHECKED with
   the error named — an endpoint that errored is not an empty result.
6. **Nothing is embedded.** OxiRS and Jena compose as ratified
   adapters over an injected transport. The engine stays outside the
   administrative domain; the governance stays inside.

## Consequences

- A national terminology server, an OxiRS knowledge base, or a Jena
  Fuseki endpoint can be adopted per-deployment as data — country- and
  company-agnostic doctrine extends to knowledge bases.
- SNOMED licence gating (terminology) and kgraph mappings compose:
  licensed content stays deployment-loaded; the seam never ships
  someone else's licensed content.
- The proof surface (kgproof) exercises eight attacks with named
  outcomes; the payload is stamped, registered in proofindex (22
  payloads), and gated by the meta exporter-determinism suite (15
  exporters).

## Alternatives considered

- **Embed oxigraph/Jena**: refused — ADR-0005, deployment weight, and
  the estate's doctrine that governed seams beat embedded engines.
- **Full SHACL/OWL reasoning in-house**: refused for now — the closed
  vocabulary covers the platform's validation needs; richer reasoning
  belongs behind a ratified external adapter where the reasoning
  engine is pinned, not reimplemented.
