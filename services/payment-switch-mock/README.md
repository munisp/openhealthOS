# Payment switch mock (Go)

Development fixture implementing the Mojaloop-pattern settlement seam:
FSPIOP-shaped `POST /quotes` → `POST /transfers` (ILP-style packet stub).

**Test fixture only — never deploy as a settlement endpoint.** Production binds
the same contract to a real switch deployment (hub + DFSP principals), which
differs per state scheme. See `openapi/claims-gateway.yaml` and ADR-0003.
