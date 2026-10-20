# ADR-0048: agentgate — one policy-bound seam for every AI/agent action

Status: accepted (2026-10-20)
Source: Chesterguan/veritas (Apache-2.0, confirmed via LICENSE file;
GitHub's NOASSERTION flag is a detection glitch). **Library not embedded
(2 stars, single author, April-2026 vintage — too young to be
load-bearing in a national health platform); runtime pattern adopted
clean-room.**

## Context

VERITAS's thesis: agent runtimes are fast and small, but regulated
environments need every action traceable, policy-constrained and
verifiable — "Linux : ZeroClaw :: RHEL : VERITAS". Its pipeline is
deterministic by construction:

    State → Policy → Capability → Propose → Verify → Transition → Audit

with a deny-by-default policy engine, hash-chained audit, and a
RequireApproval lifecycle. We already hold these doctrines in pieces —
ADR-0045 (eval gates), ADR-0046 (write validation), fraud PEND_REVIEW
(human review), step-up elevation (ADR-0039), auditlake hash chains —
but as *separate* mechanisms. Nothing enforced that a future agent
action pass through ALL of them, every time.

## Decision

`shared/agentgate` (stdlib Go): every AI/agent action executes through
`Gate.Execute(actor, action, input, propose, commit)`:

1. **Policy** — deny-by-default; unlisted action or unlisted actor
   denies. An unlisted action failing open is how temporary capabilities
   become permanent shadow ones.
2. **Propose** — the untrusted agent computes a proposal. LLM output is
   NEVER committed directly.
3. **Verify** — the trusted verifier judges the proposal (fhirguard for
   writes, PII checks for summaries); `MaxRisk` ceilings deny even
   allowed actors.
4. **Commit** — runs only on ALLOW. `RequireApproval` returns Pending
   with the proposal and defers commit to a human — the same doctrine
   as fraud PEND_REVIEW and step-up elevation: machines propose,
   people commit irreversible or clinical actions.
5. **Audit** — EVERY outcome (allow, deny, verify-reject, pend) is
   hash-chained with auditlake's law and streamed to an optional sink
   (auditlake append → Merkle-anchored off-box). A denial you can't
   audit is a control you can't prove.

`VerifyChain` gives the runtime an in-memory tamper check; the durable
guarantee is sink → auditlake anchor (innovation 10).

## Consequences

- New agent features wire one call site instead of re-implementing
  policy + verify + approval + audit four times, inconsistently.
- The gate composes, not replaces: fhirguard remains the write
  verifier *behind* the gate; ADR-0045's benchmark scores remain the
  pre-launch gate *before* an actor is added to policy at all.
- VERITAS re-evaluation trigger: if it matures (multi-maintainer,
  stable API) and our agent surface grows to justify a dedicated
  runtime, the gate's call sites make a swap mechanical.
