# ADR-0046: AI-write validation doctrine — adopted from HealthChain, not the library

Status: accepted (2026-10-20)
Source: healthchainai/HealthChain (Apache-2.0 — licence-clean, actively
maintained). Evaluated for adoption; **library rejected, doctrine adopted.**

## Context

HealthChain is a Python SDK giving AI agents typed, validated FHIR
tools: read from any EHR, write back with the right code from the right
system on the right patient with a valid status, plus a "no invented
facts" guarantee (helpers never auto-fill timestamps or statuses) and
provenance tagging of AI-touched resources.

Its flagship deployment shape — a gateway federating Epic/Cerner
sources — solves a problem we do not have: **we are the EHR.** Our
stack is Medplum plus our own shr-service; embedding a second Python
FHIR stack (pydantic, fhir.resources, FastAPI) violates the stdlib-only
doctrine (ADR-0005) and adds dependencies without adding capability.

The *doctrine*, however, closes a real hole: shr-service today accepts
any well-formed JSON. The moment a clinical-LLM feature lands (the
eval-gated kind ADR-0045 contemplates), model-authored resources would
flow into patient charts with no write-side validation.

## Decision

`packages/fhirguard` (stdlib Python, clean-room): every AI/agent-authored
FHIR write passes three gates before the SHR accepts it —

1. **Validated write.** Clinical codes only from recognised systems
   (ICD-10-CM, SNOMED CT, LOINC); subject must reference a Patient the
   SHR actually holds (an AI write may not invent a patient); status
   fields must be spec-legal FHIR R4 values, not prose.
2. **No invented facts.** Any dateTime present must be strict ISO-8601
   ("last Tuesday" is rejected); `entered-in-error` is forbidden for AI
   writes — corrections route through human review.
3. **Provenance tag.** Accepted writes get
   `meta.tag[code=ai-authored, display="AI-authored (<agent>")]` on a
   copy — the caller's object is never mutated, and the tag is
   idempotent. Same convention family as the ADR-0045 synthetic tag:
   machine-touched content is distinguishable forever.

Resource types are allowlisted (Condition, Observation,
MedicationRequest, Encounter, Patient, DocumentReference, Bundle);
novel types need human review. All violations are reported in one
error — an agent fixing one reason per retry is how error storms start.

## Consequences

- The guard is a write-side complement to ADR-0045's eval harness:
  eval gates what a model may be *trusted to attempt*; fhirguard gates
  what it may *commit*.
- shr-service integration is a middleware call; until the first
  clinical-LLM feature ships, fhirguard runs as a CLI over generated
  fixtures and as the contract tests for any future agent work.
- If HealthChain's ecosystem matures into something with pull we can't
  replicate (e.g. maintained CDS Hooks tooling), reconsideration is a
  new ADR — the doctrine boundary means the swap cost is low.
