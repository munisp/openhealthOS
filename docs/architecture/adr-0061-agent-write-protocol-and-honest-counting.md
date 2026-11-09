# ADR-0061: Approval-gated agent write protocol, conformance suite, absence envelopes, honest coverage counting — adopted from last-ehr, clean-room

Status: accepted (2026-11-09)
Source: cbetz/last-ehr (Apache-2.0 — licence-clean; **protocol and
doctrines adopted, code not embedded**: TypeScript/Next.js/npm stack
violates the stdlib-only doctrine (ADR-0005), and the reference
implementation is a 4-star alpha. The ideas are load-bearing; the
codebase is not.)

## Context

last-ehr specifies the layer ADR-0045/0046/0048 left between them: CDS
Hooks covers EHR-initiated suggestions (we adopted it in ADR-0060), the
HL7 AI Transparency IG covers after-the-fact provenance and explicitly
declines approval workflows, and neither covers the semantics of an
**agent-initiated** write a human must approve before it exists.
last-ehr's contributions we adopt:

1. **The proposal lifecycle as protocol.** Proposal (exact resource +
   exact human-readable rendering — what the reviewer sees is what
   saves) → Decision (approved / denied / unavailable as three
   distinguishable answers; only explicit approval commits; everything
   else fails closed) → Commit (exactly the proposed resource plus
   mechanical metadata only) → Audit (AIAST security label + Provenance
   resource naming agent as author and reviewer as verifier).
2. **Capability gating.** A host that cannot render proposals is never
   offered write capability at all — a degraded host cannot even ask.
3. **Policy can only deny.** Policy is evaluated before presentation,
   never substitutes for a reviewer's decision, failure denies, and
   denial reasons are static configuration text — never interpolated
   with patient data (the error path is not an exfiltration channel).
4. **Read-back verification.** After commit the store is re-read
   independently and compared with the approved proposal; the tool's
   own word is not evidence.
5. **Conformance as a suite.** A scripted reviewer drives every
   decision variant (approve, deny, unavailable, policy-denied,
   capability-absent, double-decision) and verifies persistence with
   its own reads; the report names what it cannot prove.
6. **Absence is evidence of search.** The agent must be unable to
   report an absence it never checked for — every absence claim
   enumerates its search set; unchecked stores are stated, never
   implied clear.
7. **Honest coverage counting.** "How comprehensive is it?" deserves a
   fraction with a declared, versioned denominator (US Core 9.0.0's 27
   readable resource types), per-type evidence, and gaps named — never
   an adjective, never a percentage of all of FHIR R4.

## Decision

Four packages, stdlib-only (ADR-0005), clean-room:

- **`packages/writegate`** (`writegate@1.0.0`) — the lifecycle: proposal
  with rendering + fingerprint, capability gating, policy-before-
  presentation with static denial text, decision taxonomy with correct
  attribution (reviewer vs host vs configuration), commit fidelity
  (deep-copied resource + AIAST `meta.security` label + per-session
  isolation tag), Provenance resource (agent=author, reviewer=
  verifier), independent read-back comparison, hash-chained audit of
  every outcome including refusals.
- **`packages/awpconform`** (`awpconform@1.0.0`) — the scripted-reviewer
  conformance suite over any WriteGate-shaped implementation; verifies
  with independent store reads; its report carries the stated-limits
  block (gate mechanics only — not clinical correctness, not
  authorization, not prompt-injection immunity, point-sampled
  persistence probing). The suite ships with a deliberately broken
  reference gate in its tests proving the harness catches the classic
  lie (claims commit, writes nothing).
- **`packages/absentia`** (`absentia@1.0.0`) — query envelopes: every
  result carries the stores/filters/as-of actually consulted; absence
  claims enumerate the checked set; an empty search set refuses to
  claim; unchecked stores require a stated reason.
- **`packages/fhircover`** (`fhircover@1.0.0`) — the coverage matrix
  with declared denominator + per-type evidence; off-denominator claims
  refuse; `platform_coverage.py` declares OpenHealth's honest
  27/27 via Medplum's full R4 REST surface, with the five types
  shr-service serves directly named as such.

Relationship to existing gates: charteval gates what a model may be
trusted to *attempt* (ADR-0045), fhirguard validates the *content*
(ADR-0046), agentgate gates *execution* (ADR-0048), mcptools gates the
*transport* (ADR-0060) — writegate owns the *approval lifecycle* and
awpconform proves the gate works.

## Consequences

- Any agent write path (MCP tools, future clinical-LLM features) routes
  through writegate; conformance evidence rides in CI.
- Absence claims in surveillance, allergy checks, and chart summaries
  gain a stated search set — "no known allergies" now means "searched
  these stores, at this time" and lists what was not searched.
- The coverage matrix gives partners and ministries a number with a
  denominator instead of a superlative.
