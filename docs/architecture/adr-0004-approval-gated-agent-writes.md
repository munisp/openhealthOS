# ADR-0004: Approval-Gated Agent Writes — AI never writes unreviewed

- Status: Accepted
- Date: 2026-10-17
- Spec reference: §5.10 (Last EHR protocol), failure mode 9 (trust deficit)

## Context

Clinicians perceive AI as a threat; patients distrust algorithms trained on foreign datasets. Automation that writes directly into a health record creates unreviewed burden and destroys trust — the failure mode that kills adoption. The Last EHR project (Apache-2.0) defines an Approval-Gated Agent Writes protocol: **Proposal → Decision → Commit → Audit**.

## Decision

The protocol is the standard for **every** AI/agent write into the record:

1. **Proposal** — the model produces a structured proposal (e.g. triage assessment, suggested actions, coding suggestions). It has no write path to the record. Models can never fake an absence: a proposal with no decision is nothing.
2. **Decision** — a human clinician approves, edits, or rejects. Decision capture is part of the clinical UI, not a side channel.
3. **Commit** — only an approved (or edited) proposal is committed to the record, attributed to the deciding clinician with the model's proposal attached as provenance.
4. **Audit** — proposal, decision, diff and commit are appended to the immutable audit log; the full chain is replayable.

Reference implementation: `packages/triage` (protocol-guided AI triage for nurses/CHWs, tuned to Nigeria's top-20 presentations) with conformance tests proving no commit occurs without a decision.

## Consequences

- AI is nurse-first decision support; automation never adds unreviewed burden.
- Every AI-assisted record entry is attributable and contestable — an NDPA and clinical-governance requirement.
- Models train on local gold-table data (lakehouse plane), addressing the foreign-dataset trust deficit.
