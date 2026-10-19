# ADR-0036: Production plane completion — one front door, every service in the plane, no port collisions

Status: accepted (2026-10-19)

## Context

A production-readiness audit (all 34 Go modules, all Python packages,
all JS app suites green) found the remaining gaps were structural, not
functional:

1. **Port collision.** `dispatch-service` and `ipd-service` both
   defaulted to `:8093`; `lga-hub` and `wellness-service` both to
   `:8090`. Two platform services on one port is a deployment-time
   coin toss — whichever binds first wins, the other crashes, and the
   crash message never mentions the real cause.
2. **The single-entry doctrine leaked.** Seven services (scheduling,
   dispatch, telemed, media, imaging, training, ipd) were reachable
   only on their own ports. The gateway is supposed to be the only
   public door; every unrouted service is an unauthenticated,
   un-metered, un-screened private entrance.
3. **The compose plane was incomplete** — ten services absent from
   `platform.yaml`, so `docker compose up` did not give you the
   platform, it gave you two-thirds of it.
4. **The k8s services plane stopped at nine workloads** — everything
   built after ADR-0019 existed only as source.

## Decisions

### 1. Ports are a registry, not a coincidence

`ipd-service` moves to `:8098`, `lga-hub` (edge plane) to `:8091`.
The full allocation is now collision-free across both planes:

| Port | Service | Plane |
|------|---------|-------|
| 8000 | api-gateway | platform (public) |
| 8080–8089 | claims, registry, consent, ussd, wallet, shr, equipment, telehealth, pharmacy, notify | platform |
| 8090 | wellness-service | platform |
| 8091 | lga-hub | edge/LGA |
| 8092–8098 | scheduling, dispatch, telemed, media, imaging, training, ipd | platform |
| 8443 | sync-ingest | platform |

### 2. The gateway route table is the public contract

`DefaultRoutes` gains `/api/scheduling`, `/api/dispatch`,
`/api/telemed`, `/api/media`, `/api/imaging`, `/api/training`,
`/api/ipd` (wellness was routed with ADR-0035). A new regression
guard, `routes_test.go`, fails CI if:

- any platform service lacks a gateway route (the coverage test lists
  every service by name — adding a service without a route is a red
  build, not a silent omission);
- two routes share a prefix (first-match-wins makes a duplicate a
  silent shadow);
- a default upstream port drifts from the service's `LISTEN` default.

### 3. The compose plane is the whole plane

`platform.yaml` gains scheduling, dispatch, telemed, media, imaging,
training, ipd and wellness with their wiring (registry/notify/shr/
eligibility upstreams, `media-blobs` and `wellness-journal` volumes),
and the gateway's environment and `depends_on` name all of them.
27 services, one command, no private doors. Optional integrations stay
honest: telemed without LiveKit keys runs store-and-forward; imaging
without `ORTHANC_URL` runs worklist-and-priors — the seam degrades, it
never pretends.

### 4. k8s catches up

`infra/k8s/services-plane-extended.yaml` adds the eight missing
Deployments + Services under the same hardening contract (non-root,
read-only rootfs, dropped capabilities, fail-closed `AUTH_REQUIRED`,
seccomp RuntimeDefault). Journal-carrying services (media, wellness)
get `emptyDir` marked demo-grade with a PVC note, matching the
precedent set by wallet-service. The gateway Deployment's env names
every extended-plane upstream.

## Consequences

- Production rollout is one compose file or three k8s manifests, not a
  scavenger hunt.
- Any future service ships with a port, a route, a compose entry and a
  k8s workload — the route-coverage test enforces the second, and this
  ADR names the rest.
- The port table above is the registry; new services take the next
  free port and update the table in the same commit.
