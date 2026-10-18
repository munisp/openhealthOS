# ADR-0013: Hardening wave and innovation layer — encryption at rest, conformance gates, and the premiere-tier feature set

**Status**: accepted · **Date**: 2026-10-18

## Context

With the national-scale spine (ADR-0011) and the identity/payer/ERP
connectivity (ADR-0012) in place, two review questions remained:

1. **Would the platform survive a hostile audit?** Offline-first means
   PHI sits on facility devices and in browser storage; OIDC was
   optional; sync events were schemaless; money retries relied on
   in-memory idempotency (lost on restart); the "Medplum as FHIR
   foundation" and "OpenHIE-pattern federation" claims were asserted,
   not gated.
2. **What makes this the premiere platform, not merely a complete one?**
   Ten improvements (audit hardening) and ten innovations
   (differentiation) were proposed; this ADR records the decisions and
   where each lives.

## Decision

### A. Encryption at rest at the edge — `shared/sealx` + PWA WebCrypto

- `shared/sealx`: AES-256-GCM envelope encryption for the sync-agent
  outbox. Key from `SEALX_KEY` (hex) or `SEALX_KEY_FILE`; unset is a
  loud plaintext dev mode, never silent. Sealed files carry the `SX1:`
  prefix; the wire format stays plain JSONL (decryption happens before
  batching), so the ingest path is unchanged. Tampered or wrong-key
  ciphertext fails closed — the event is retained, never dropped.
- `apps/pwa/js/crypto.js`: the same construction over WebCrypto
  (`SXW1` envelope). The device key is generated per-browser and stored
  in the IndexedDB meta store. Legacy plaintext rows remain readable
  (passthrough), so existing deployments upgrade without migration.
- **Honest threat model**: this protects against lost-device /
  stolen-disk extraction. It does not defend against a rooted device or
  XSS — those need OS-level controls, which the deployment runbook
  owns.

### B. Fail-closed identity — `AUTH_REQUIRED=1`

`shared/authz.RequireFromEnv`: when `AUTH_REQUIRED=1` and no
`OIDC_ISSUER` is configured, the service **refuses to boot**. A
misconfigured deployment must fail loudly, never silently open. The
k8s plane sets `AUTH_REQUIRED=1` with the issuer from the
`openhealth-idp` ConfigMap on every state-plane service. USSD is the
deliberate exception (telco-terminated; identity is the MSISDN; replies
carry no clinical detail).

### C. Schemaless no longer — `shared/eventschema`

Sync events are validated at ingest (`type`, `ts`, `payload` required).
The national-sync demo payloads were corrected to comply — the schema
gate is wired into sync-ingest, not documented aspirations.

### D. Money idempotency survives restart — migration `0006`

`wallet_idem` (ref → posted txid) in the state plane. Every premium
collection and claim payout carries a caller-supplied ref; a retried
request is answered from the record, never re-posted. Wired in compose
and k8s (`openhealth-migrate-0006`).

### E. Event-driven FHIR intake — `/fhir/inbox` + the Medplum gate

When Medplum is the production FHIR server, its Subscriptions POST
resources to the notification service the moment they are created —
the notification rail becomes event-driven off the clinical record
itself, with no polling, and works for any FHIR source (Medplum,
OpenMRS FHIR2, another state's HIE). Mapping is deliberately narrow
(laboratory Observation → "result ready"; MedicationRequest → "medication
ready"; Appointment → reminder; everything else ignored) and carries no
clinical detail.

**The claim is gated, not asserted**: `tools/conformance/medplum_gate.py`
creates real rest-hook Subscriptions on a real Medplum server and
asserts delivery (`.github/workflows/medplum-gate.yml`, Docker service
containers). The harness is validated offline via `--selftest` (mock
Medplum, real notification rail) in the main CI.

### F. OpenHIE-pattern conformance — `tools/conformance/conformance.py`

Five workflows run against booted services on every push: register →
FHIR Patient; save → FHIR Encounter; **cross-facility read without
consent refused** (the consent gate is the federation contract — a leak
fails the build); consented read allowed; facility as FHIR Location.
The SHR fails closed when the consent service is unreachable — a missing
consent check must never become an access grant.

### G. Patients on feature phones can read — USSD clinic updates

USSD menu option 3 ("My clinic updates") reads the notification rail's
patient feed, keyed on the telco-presented MSISDN plus the patient's
clinic code. A mismatch returns a generic "no updates" — never
revealing whether a code exists (no enumeration). Replies fit one USSD
screen (≤182 chars), newest first, no clinical detail.

### H. Payer transparency — `/payers/stats` + the payer dashboard

The claims orchestrator tallies decisions per payer (claims, approvals,
pends, denials, payable ₦) from the payer-router scheme map; the
operations dashboard renders a payer view (approval mix, payable share,
unmapped bucket). This is the number a state health agency negotiates
with.

### I. Proof tooling — load, DR, accessibility

- `tools/syncbench` — sync load/chaos harness.
- `tools/dr-drill.sh` — disaster-recovery drill against the backup
  runbook.
- `tools/a11y-contrast.py` — WCAG contrast audit for the app CSS,
  CI-checked.

### J. The innovation layer — ten stdlib-only packages/tools

Each is a seam with deterministic, testable logic — no vendor lock-in,
no licence risk (ADR-0005), all green in CI:

| Innovation | Where | What it proves |
|---|---|---|
| IPS QR patient summary | `packages/ips` + `tools/ipsqr` | a patient's International Patient Summary as a signed QR — continuity of care on paper |
| Syndromic surveillance | `packages/surveillance` | outbreak signals from facility events, privacy-preserving |
| Edge AI copilot | `packages/edgeai` | offline decision-support drafting, approval-gated (ADR-0004) |
| Provider verifiable credentials | `tools/provcred` | signed, offline-verifiable clinician credentials |
| Fraud ML | `packages/frauddetect` | claim-fraud scoring as an advisory signal, never an auto-deny |
| Household mapping | `packages/households` | CHW catchment geography from registrations |
| Payment rails | `packages/payrails` | PSP-agnostic collection/disbursement seam |
| Predictive cold chain | `packages/coldchain` | spoilage risk from telemetry before the vaccine dies |
| OpenHIE conformance | `tools/conformance` | §F — the federation contract, gated |
| Payer dashboard | §H | payer-mix transparency for agencies |

## Consequences

- Every security and interoperability claim in the README now has a
  gate that fails the build: conformance, Medplum, licence hygiene,
  FHIR profile validation, self-contained app shells.
- Edge encryption is opt-in by env var; dev stays plaintext with a loud
  log line. Production deployments set `SEALX_KEY_FILE` via the secret
  plane.
- The innovation packages are additive seams — none of them is on the
  critical path of care, so an innovation bug can never stop a clinic.
