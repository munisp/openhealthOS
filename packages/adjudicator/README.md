# Adjudicator (Python) — claims rules engine

Deterministic, auditable claims adjudication: **eligibility → benefit →
tariff → duplicate/fraud flags → APPROVE / PEND / DENY**. Every rule that
fires returns a reason or flag code; decisions are events, never silent
verdicts.

Clean-room re-implementation of the adjudication pipeline pattern (ADR-0005):
the BSL-licensed reference design is never embedded; this engine is ours,
AGPL, and the seam the Nigerian Million-Claim Challenge benchmarks against.

## Interface

```
claim JSON on stdin → decision JSON on stdout
```

`history` on the claim carries recent prior claims for the 14-day duplicate
window. Registries (members, benefits, tariffs) are in-file fixtures here;
production reads them from the FHIR/claims services over Dapr/Kafka.

## Test

```bash
python3 -m unittest discover -s packages/adjudicator
```
