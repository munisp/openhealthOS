# Runbook: deploy a state plane

Audience: platform engineer standing up one state's namespace. Time: ~1 h
excluding Postgres HA and ingress certificate provisioning.

## Prerequisites

- Kubernetes ≥1.28 with a CNI that enforces NetworkPolicy (Calico/Cilium).
- Postgres 16 reachable from the cluster (HA operator recommended; a
  single instance is acceptable for pilot scale).
- Keycloak ≥24 reachable from the cluster.
- Container images built and pushed:
  `ghcr.io/munisp/openhealthos/<service>:<tag>` for all services in
  `infra/k8s/state-plane.yaml` and `services-plane.yaml`.

## 1. Namespace and security floor

```sh
kubectl apply -f infra/k8s/namespace.yaml      # PSA restricted labels
# edit namespace + labels for your state, then:
kubectl apply -f infra/k8s/hardening.yaml      # NetworkPolicies + migrate job
```

## 2. Secrets (never commit real values)

```sh
kubectl -n oh-state-example create secret generic openhealth-db \
  --from-literal=url='postgres://openhealth:STRONG@postgres:5432/openhealth'
kubectl -n oh-state-example create secret generic consent-keys \
  --from-literal=hmac-key="$(openssl rand -hex 32)"
```

## 3. Database migrations

```sh
kubectl -n oh-state-example create configmap openhealth-migrations \
  --from-file=db/migrations/
kubectl apply -f infra/k8s/hardening.yaml   # runs job/openhealth-migrate-0002
kubectl -n oh-state-example logs job/openhealth-migrate-0002
```

The job runs `ON_ERROR_STOP=1`; a failed migration fails the job — fix and
re-apply, do not skip.

## 4. Identity

1. Import `infra/keycloak/realm-openhealth.json` into Keycloak
   (realm `openhealth`).
2. Rotate the `openhealth-gateway` client secret
   (shipped value is `change-me-in-production`).
3. Create users, assign realm roles, and set the `facility_id` user
   attribute — it maps into tokens as the `facility_id` claim.
4. Set on every authenticated Deployment:

```yaml
- { name: OIDC_ISSUER,   value: "https://keycloak.example/realms/openhealth" }
- { name: OIDC_AUDIENCE, value: "openhealth" }
```

## 5. Workloads

```sh
kubectl apply -f infra/k8s/state-plane.yaml
kubectl apply -f infra/k8s/services-plane.yaml
kubectl -n oh-state-example rollout status deploy --timeout=180s
```

**Before production traffic:** replace the two `emptyDir` ledger volumes
(claims-orchestrator, wallet-service) with PVCs. `emptyDir` loses the
ledger journal on pod reschedule — see ADR-0007.

## 6. Verify

- Every pod: `kubectl port-forward` then `GET /healthz` → `ok`,
  `GET /metrics` → counters.
- `GET /api/...` without a token → 401; with a realm token → 200.
- Pilot gates: run `python3 packages/analytics/reporter.py` against the
  environment (see `docs/runbooks/pilot-90-day-go-live.md`).

## Rollback

Deployments are stateless except the ledger volumes. Roll back with
`kubectl rollout undo deploy/<name>`; database migrations are additive
only (never destructive within a pilot), so no down-migration is needed.
