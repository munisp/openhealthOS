# Registry service (Go)

OpenHIE-aligned registries (ADR-0002): **facilities** and **providers**, with the
physical-infrastructure profile (power, water, ownership, BHCPF status, geo
position) carried on every facility record — the data facility-readiness
dashboards are built from.

## API

| Endpoint | Shape |
|---|---|
| `GET/POST /facilities` · `GET /facilities/{id}` | internal registry shape |
| `GET/POST /providers` · `GET /providers/{id}` | internal registry shape |
| `GET /fhir/Location/{id}` | FHIR R4 Location (infrastructure profile as extensions) |
| `GET /fhir/Practitioner/{id}` | FHIR R4 Practitioner |
| `GET /healthz` | liveness |

Storage is a seam (`Store` interface): in-memory for edge nodes and tests,
Postgres on the state plane in production. Patient identity (NIN-bound where
available, locally-resolvable offline IDs otherwise) plugs into the same
service per ADR-0002.

## Run / test

```bash
LISTEN=:8081 go run .
go test ./...
```
