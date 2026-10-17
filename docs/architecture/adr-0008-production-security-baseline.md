# ADR-0008: Production security baseline — identity, pod security, default-deny network

**Status**: accepted · **Date**: 2026-10-18

## Context

Through the pilot milestones the services ran with no authentication and
an open network: acceptable for a localhost demo, disqualifying for a
platform that moves money and holds patient records. The question was not
*whether* to harden but *where the floor sits* — the set of controls that
is always on, below which no environment may call itself production.

Constraints carried in from earlier ADRs: stdlib-only implementation
(ADR-0005), upstream identity via Keycloak rather than a hand-rolled IdP
(ADR-0002's compose-don't-fork doctrine), and edge deployments that must
still run with zero connectivity (ADR-0001).

## Decision

**The production floor is four controls, all declarative and all in the
repo.**

1. **OIDC on every service that is not a webhook or fixture.**
   `shared/authz` verifies RS256 bearer tokens against the issuer's JWKS
   (stdlib-only: no JWT library, no alg-confusion surface — anything but
   RS256 is rejected structurally). `exp`, `iss`, and `aud` are enforced;
   unknown `kid` forces a key refresh, so Keycloak rotation needs no
   redeploy. `/healthz` and `/metrics` bypass auth so probes and
   Prometheus keep working. Identity is opt-in per process via
   `OIDC_ISSUER`/`OIDC_AUDIENCE`: **edge facilities run without it**
   (offline-first means a dead token issuer must never block care), and
   the state plane sets it always. The realm is code:
   `infra/keycloak/realm-openhealth.json`, imported by compose and
   installable as-is into any Keycloak.
2. **Restricted pod security.** Namespace enforces PSA `restricted`;
   every pod runs `seccompProfile: RuntimeDefault`, every container
   `runAsNonRoot`, `allowPrivilegeEscalation: false`,
   `readOnlyRootFilesystem: true`, `capabilities: drop: [ALL]`. Writable
   paths (`/tmp`, ledger data) are explicit volumes — nothing else can be
   written, which also makes accidental state-in-container impossible to
   miss.
3. **Default-deny network.** `hardening.yaml` denies all pod-to-pod
   traffic, then opens exactly four flows: same-namespace east-west,
   ingress → claims-orchestrator, monitoring → `/metrics` ports, and
   egress limited to DNS + in-cluster. A new service is unreachable until
   its flow is declared — that friction is the point.
4. **Secrets never in manifests.** Database URLs and the consent HMAC
   key come from Secrets (`openhealth-db`, `consent-keys`); the migration
   job reads the same Secret, so there is one source of truth for the
   connection string.

## Consequences

- A developer `docker compose up` still works with zero identity setup;
  production is the delta, not a rewrite.
- Any container that tries to write outside its declared volumes crashes
  loudly at deploy time, not silently in production.
- The ledger's `LEDGER_DATA_DIR` volumes are `emptyDir` in the committed
  manifests — **demo-grade**. Production must substitute PVCs (or run the
  ledger on TigerBeetle per ADR-0003); the manifests say so in comments
  so the substitution cannot be forgotten quietly.
- Keycloak realm export carries a placeholder gateway secret
  (`change-me-in-production`); rotating it is step one of the deploy
  runbook.
