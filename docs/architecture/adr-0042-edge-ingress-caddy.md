# ADR-0042: Edge ingress — Caddy replaces nginx on facility nodes

Status: accepted (2026-10-20)

## Context

The edge node (ADR-0001) served the PWA from bare nginx:80 — no TLS, no
security headers, hand-maintained nginx.conf. PHI on a facility LAN in
plaintext fails the NDPA 2023 duty of care even before the internet is
involved. Meanwhile APISIX already owns the API-gateway role on the
state plane, and we do not want a second Lua/etcd stack on a 4-core
edge box.

## Decision

Caddy is the edge ingress. Scope, honestly:

- **Edge/LGA nodes: Caddy.** Single static binary (~40 MB), atomic
  config reloads, crash-safe storage. `tls internal` runs a local CA —
  the offline-correct answer to TLS on a LAN with no public ACME; the
  CA root ships to devices via the same provisioning channel that
  installs the PWA. Nodes that gain a public hostname (LGA hubs) flip
  one line to real ACME.
- **State/national plane: APISIX stays.** Caddy is not a replacement
  API gateway (no plugin ecosystem parity, no etcd-backed dynamic
  routing). APISIX keeps OIDC-to-Keycloak, rate limits and tenant
  routing; Caddy never fronts it.

## Integration with the existing stack

- **APISIX:** none by design — different layers. Edge Caddy proxies only
  the node's own stdlib-Go services (`/api/* → sync-agent:8080`).
  Anything destined for the state plane leaves via the sync agent's
  store-and-forward uplink, which terminates at APISIX (mTLS, ADR-0025).
- **Keycloak:** unchanged. Interactive SSO lives on the state plane
  behind APISIX's OIDC plugin. Edge nodes authenticate by device
  provisioning cert, not browser SSO — offline-first means the login
  must not depend on a reachable IdP.
- **open-appsec:** at the state plane it attaches to APISIX/ingress as
  today. On the edge, Caddy's surface is static files + three local
  Go APIs behind a LAN — we do not run the open-appsec agent on 4-core
  edge hardware; the compensating controls are the CSP/HSTS headers in
  the Caddyfile, the LAN trust boundary, and the fact that edge APIs
  are stdlib Go with no query surface beyond their fixed routes. This
  is documented as a conscious trade-off, not an oversight.
- **Observability (ADR-0040):** Caddy's JSON access log rolls to
  `/var/log/caddy` and ships via the telemetry-collector outbox like
  every other edge signal. Prometheus scraping of Caddy's admin metrics
  is off by default (admin endpoint disabled for attack surface); the
  log-derived counters are sufficient at this tier.

## Consequences

- One less config language (nginx.conf gone), one static binary to
  ship. Config is 60 lines, reviewable in one screen.
- Devices must trust the edge CA — a provisioning step we already own.
- If a facility graduates to always-online, the same Caddyfile gains
  a domain line and public ACME with no other changes.
