# ADR-0062: agentwrite — one pipeline for every agent read and write

Status: accepted (2026-11-10)
Builds on: ADR-0046 (fhirguard), ADR-0048 (agentgate), ADR-0060
(cdshooks + mcptools), ADR-0061 (writegate + absentia + fhircover)

## Context

The platform's agent-safety doctrines landed as five separate
packages, each correct on its own terms:

| package | owns |
|---|---|
| mcptools | the transport: deny-by-default, write pends, audit |
| writegate | the proposal lifecycle: capability gating, decision, commit, read-back |
| fhirguard | the content: codes, subjects, statuses, no invented facts |
| absentia | the read side: absence = evidence of search |
| fhircover | the numbers: coverage with a declared denominator |

Separate mechanisms share a known failure mode: an agent action can
slip BETWEEN them — a write tool that pends but never reaches a
proposal, a read that answers without naming its search set, a
capability list that shows write tools to a host that cannot render
them. Nothing enforced that every agent action travel ALL of them,
every time.

## Decision

`packages/agentwrite` (agentwrite@1.0.0, stdlib Python) is the
composition layer. `AgentWriteStack` holds one store, one
ToolRegistry, one WriteGate, and wires:

- **Read** = `registry.call` whose result is an absentia
  `QueryEnvelope` (subject + as-of + search set + declared not-checked
  stores). An empty result set yields an `absence` claim that names
  searched vs not-checked; an unallowlisted actor gets neither result
  nor envelope.
- **Write** = `registry.call` pends (audited) → the stack's builder
  constructs `{resource, rendering}` (builders NEVER commit) →
  fhirguard validates and returns its tagged copy → writegate proposes
  the tagged copy with fhirguard installed as a PRE-PRESENTATION
  policy → human decision → writegate commits (exact resource +
  AIAST + ai-authored tag + session tag + Provenance) → independent
  read-back.
- **Capability gating at discovery**: `list_tools(host_can_render=
  False)` omits write tools entirely — a degraded host is not refused,
  it is never shown the pen.
- **Policy denial text is static** (FHIRGUARD_DENIAL); fhirguard's
  field-level problems travel separately to the calling agent as
  repair detail. The error path is not an exfiltration channel, and an
  agent is not asked to fix its write one vague denial at a time.
- **Coverage** is exposed through fhircover's platform matrix
  (27/27 against us-core-9.0.0) — a fraction with a denominator, never
  an adjective.
- **Three audit chains** (transport, gate, stack) must ALL verify;
  `verify_audit()` fails if any one is tampered.

Composition, not modification: no existing package's internals were
edited; the stack speaks to public contracts only. New agent features
register tools against the stack, once, instead of re-assembling five
governances inconsistently.

## Consequences

- The committed resource carries BOTH provenance markers: fhirguard's
  `ai-authored` tag (proposed resource = tagged copy, so what the
  reviewer approves is what commits, tag included) and writegate's
  AIAST security label + Provenance resource.
- A write tool registered without a builder errors loudly at call time
  — a pend nothing can construct is refused, not queued.
- The Go runtime (`shared/agentgate`) remains the service-side
  execution gate; this stack is the Python package-side pipeline. A
  future service adapter passes the same contracts over the wire.
- Demo: `packages/agentwrite/demo_agentwrite.py` seeds the
  deterministic morning (one approval with read-back, one pre-
  presentation policy denial, one reviewer denial, one absence claim
  with a stated gap) into `apps/pwa/agentwrite.json`, rendered by
  `apps/pwa/agentwrite.html` in the offline shell.
