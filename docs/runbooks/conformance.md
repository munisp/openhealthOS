# Runbook: conformance gates

Two harnesses prove the federation and FHIR-foundation contracts. Both
exit non-zero with the failing step named; both run in CI on every push.

## OpenHIE-pattern conformance — `tools/conformance/conformance.py`

Boots nothing itself; point it at running services:

```sh
python3 tools/conformance/conformance.py \
  --registry http://127.0.0.1:8081 --shr http://127.0.0.1:8085 \
  --consent http://127.0.0.1:8082
```

| Workflow | Contract proven |
|---|---|
| W1 | patient registers and reads back as FHIR R4 Patient |
| W2 | clinical encounter stores and reads back as FHIR Encounter |
| W3 | **cross-facility read without consent is refused (403)** — the federation contract |
| W4 | consented read succeeds (consent artefact → `/consents/check` → SHR gate) |
| W5 | facility is queryable as FHIR Location (power/water readiness profile) |

If W3 ever passes a read without consent, treat as a sev-1: the consent
gate is leaking. The SHR fails closed when the consent service is
unreachable — an outage denies cross-facility reads, never grants them.

## Medplum subscription gate — `tools/conformance/medplum_gate.py`

Proves the notification rail is event-driven off a real Medplum FHIR
server (ADR-0013 §E). CI: `.github/workflows/medplum-gate.yml` boots
postgres + redis + `medplum/medplum-server` and runs:

```sh
python3 tools/conformance/medplum_gate.py \
  --medplum http://localhost:8103 \
  --openhealth http://127.0.0.1:18090 \
  --inbox-endpoint http://host.docker.internal:18090/fhir/inbox
```

- `--inbox-endpoint` is the `/fhir/inbox` URL **as reachable from the
  Medplum server** (in Docker: the host-gateway address; in production:
  the gateway's public `/api/fhir/inbox` route).
- Default credentials are the Medplum seed admin (`admin@example.com` /
  `medplum_admin`) — override with `--email/--password`.
- `--selftest` runs the same assertions against an in-process mock
  Medplum (offline; validates the harness itself — this is what the main
  CI workflow runs).
- First Medplum boot takes minutes (DB migrations + seed); the workflow
  waits on `/healthcheck`.
