# Triage (Python) — protocol-guided, approval-gated

Nurse/CHW-first triage decision support tuned to Nigeria's top PHC
presentations (severe malaria, pneumonia, severe dehydration, obstetric
danger signs, severe acute malnutrition — IMCI-aligned protocols), wrapped in
the **Approval-Gated Agent Writes** protocol (ADR-0004):

```
Proposal → Decision → Commit → Audit
```

- The engine can only **propose**. It has no write path to the record.
- A human clinician **decides** (approve / edit / reject). A deciding
  clinician id is mandatory — accountability is not optional.
- Only an approved or edited proposal **commits**, carrying full provenance
  (proposed-by, decided-by, proposal digest, edited flag).
- Every transition lands in an append-only **audit** log; a rejected proposal
  can never be committed.

The conformance tests are the contract: `test_commit_without_decision_is_impossible`,
`test_rejected_proposal_never_commits`, `test_audit_chain_is_complete`.

## Test

```bash
python3 -m unittest discover -s packages/triage
```
