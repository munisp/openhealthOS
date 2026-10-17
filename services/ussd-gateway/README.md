# USSD gateway (Go) — the feature-phone channel

Where data is absent, USSD/SMS carries enrolment and eligibility (ADR-0001).
A session is a state machine over cumulative telco input (`1*NG-0001`); the
coverage verdict comes from the same eligibility seam as the claims gateway —
one answer, every channel.

- `POST /ussd` — `{session_id, msisdn, text}` → `CON …` / `END …` reply
- Sessions terminate cleanly; unknown member IDs route to enrolment guidance.

The default binary wires a fixture checker; production binds the eligibility
contract (`openapi/claims-gateway.yaml` → `services/claims-orchestrator`).

```bash
go run . && go test ./...
```
