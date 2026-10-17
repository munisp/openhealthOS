# authz

OIDC bearer-token verification for the gateways — **stdlib-only**
(crypto/rsa + JWKS over HTTP), per ADR-0005: security-critical paths carry
no third-party dependency we cannot audit line by line.

- RS256 only — algorithm-confusion tokens (`HS256`, `none`) are rejected.
- JWKS fetched from the issuer (`/protocol/openid-connect/certs`,
  Keycloak-compatible), cached 5 min, with one forced refresh on unknown
  `kid` so key rotation never rejects valid tokens.
- Signature, expiry, issuer, and audience all verified; verified claims
  (including the realm-mapped `facility_id`) are injected into the request
  context for downstream attribution.
- `/healthz` bypasses auth — probes are infrastructure, not data access.

## Wiring (per service)

```go
handler := mux
if v := authz.FromEnv(os.Getenv); v != nil {
    handler = v.Middleware(mux) // OIDC_ISSUER set → enforced
    log.Print("oidc: enforcing bearer auth")
}
log.Fatal(http.ListenAndServe(addr, handler))
```

Unset `OIDC_ISSUER` keeps the service open for edge development.
Enforcement is all-or-nothing per process: a half-authenticated gateway
is worse than a clearly-open dev box.

Consumers add:

```
require github.com/munisp/openhealthOS/shared/authz v0.0.0
replace github.com/munisp/openhealthOS/shared/authz => ../../shared/authz
```

```bash
go test .   # mints real RSA keys in-test; no network needed
```
