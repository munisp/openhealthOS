# ADR-0063: Threat hardening posture — DDoS, MITM, insider threats

Date: 2026-10-20 · Status: accepted · Builds on ADR-0008 baseline

## Context

ADR-0008 set the production floor (OIDC, restricted pods, default-deny
network). A national health platform then faces the named threats: DDoS
against payer/clinical APIs, man-in-the-middle against facility uplinks and
east-west traffic, and insiders — historically the largest source of health
data breaches. Posture must be controls-in-repo, not prose.

## Decision

1. **DDoS (external)**: L7 per-IP rate + connection caps and UA shedding at
   APISIX global rules; per-consumer token buckets per route; 1MB body caps
   at BOTH the gateway and the facility edge (payload bombs die at the
   perimeter of either tier); honest 429s everywhere. L3/4 volumetric
   absorption is explicitly delegated to the state-plane provider/LB — an
   honest boundary, documented not claimed.
2. **MITM**: TLS floor 1.2/prefer 1.3 at edge (Caddy `tls protocols`) and
   gateway; HSTS + no Server banner + CSP baseline; internal CA roots via
   MDM for facility LANs (no skip-verify anywhere); WireGuard pod-to-pod
   east-west (ADR-0043); HMAC-signed webhooks (ADR-0061). This change also
   RESTORES the HTTP/3 `protocols h1 h2 h3` servers block and the 443/udp
   mapping lost in a stash incident — now test-locked so it cannot silently
   regress again.
3. **Insider**: `packages/secguard` — five detection rules (mass-export,
   off-hours-bulk, breakglass-burst, scope-escalation, dormant-sudden) over
   the existing audit lakes; tunable thresholds; **findings, never
   auto-bans** (auto-blocking a clinician can kill a patient); exit 1 for
   CI/nightly cron.
4. **Test-locked posture**: test_apisix_config.py gained DDoS gates;
   test_caddyfile.py locks headers, TLS floor, HTTP/3, no-store, body cap.
   Configuration drift now fails CI instead of becoming an incident.

## Consequences

- Positive: the three named threat classes each have layered controls with
  executable evidence; the security posture doc (docs/security-posture.md)
  maps every threat to its control and proof.
- Limitations (honest): secguard runs batch/nightly — streaming detection is
  the next iteration; APISIX ua-restriction sheds only cheap bots;
  Wazuh/IDS tuning is per-deployment; no WAF signatures (OpenResty WAF
  rulesets are a deployer option, not embedded).
