# ADR-0060: HealthChain surface review — CDS Hooks + governed agent tools adopted clean-room; CDA and gateway federation refused

Status: accepted (2026-11-09)
Source: healthchainai/HealthChain (Apache-2.0). ADR-0046 adopted its
write-validation doctrine (`fhirguard`) and rejected embedding the
library. This ADR closes the loop ADR-0046 explicitly left open: "if
HealthChain's ecosystem matures into something with pull we can't
replicate (e.g. maintained CDS Hooks tooling), reconsideration is a
new ADR."

## Context

HealthChain's remaining surface after ADR-0046: (1) CDS Hooks services
(gateway/cds), (2) agent tool exposure over MCP/LangChain
(tools/toolkit), (3) CDA document generation/parsing (interop),
(4) multi-EHR FHIR gateway federation (gateway), (5) pipeline and
sandbox frameworks.

Assessment:

- **CDS Hooks** — real pull. Our decision logic (earlywarn NEWS2,
  drugkb interactions and renal dosing, sepsis clocks, pathways) had
  no standard exposure; it computed in packages and surfaced nowhere a
  viewer or external EHR could consume. CDS Hooks 2.0 is the one
  protocol Epic, Cerner, and Medplum all speak.
- **MCP-style tool surface** — real pull, dangerous ungoverned. Agents
  need typed tools with structured errors; an ungoverned surface is an
  unaccountable writer. ADR-0048's gate law applies.
- **CDA** — refused. We ship FHIR IPS + SMART Health Links
  (packages/ips); CDA is a one-way legacy bridge no partner has
  requested. If a national rail requires it, that requirement is a new
  ADR with the partner named.
- **Gateway federation** — refused again (ADR-0046 stands): we are the
  EHR. Peer FHIR interop already exists as targeted bridges
  (adapters/openmrs, dhis2-bridge, lis-bridge).
- **Pipeline/sandbox frameworks** — refused: our pipeline patterns and
  synthetic fixtures (synthseed, graphseed, localepack) already cover
  the need without a second framework.

## Decision

- **`packages/cdshooks`** (stdlib, clean-room): hook registry as data
  (patient-view, order-select, order-sign, encounter-discharge), cards
  with summary/indicator/source contracts, prefetch as a declared
  contract (missing keys refuse, naming them), four-eyes service
  ratification (unratified services invisible to discovery and refuse
  to run), hash-chained audit of every call including refusals.
  Built-in adapters wire `earlywarn.news2` (patient-view),
  `drugkb.check_order` (order-sign), and `drugkb.renal_verdict`
  (order-sign). Adapter failure is loud — a 502 with a reason, never
  a fabricated card.
- **`packages/mcptools`** (stdlib, clean-room): MCP-style tool
  registry over newline-delimited JSON-RPC 2.0 (tools/list,
  tools/call). Deny-by-default actors and tools; risk classes —
  "read" serves allowlisted actors, "write" returns PENDING for human
  commit (machines propose, people commit); structured multi-violation
  errors; hash-chained audit of allow/deny/pend/error.

Both refuse third-party dependencies; both are contracts other
packages compose with, not frameworks that own them.

## Consequences

- Any viewer (PWA, patient app, external EHR embedding us) can consume
  platform decision support through a standard discovery + call shape.
- Future clinical-LLM features attach through mcptools and inherit
  deny-by-default, pend-on-write, and the audit chain for free.
- The fhirguard + charteval + mcptools + agentgate stack is now
  complete: eval gates what a model may attempt (ADR-0045), fhirguard
  gates what it may commit (ADR-0046), agentgate gates how it executes
  (ADR-0048), mcptools gates how it is reached (this ADR).
