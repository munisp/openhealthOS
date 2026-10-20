# Security posture — threat model and hardening matrix

Date: 2026-10-20 · Companions: ADR-0008 (baseline), ADR-0039 (step-up),
ADR-0043 (Cilium), ADR-0055 (QR trust), ADR-0062 (APISIX), ADR-0063 (this
posture). Every control below is in-repo and test- or config-gated.

## Threat matrix

### 1. External threats

| Threat | Layers (defence in depth) | Where implemented | Evidence |
|---|---|---|---|
| **DDoS — volumetric (L3/4)** | Absorb upstream of us: state-plane ingress behind provider Anti-DDoS; QUIC's connection migration resists reflection amplification; UDP/443 rate-shaped at the LB | infra/edge/Caddyfile `servers :443`, ops runbook | chaos runbook scenario 2 |
| **DDoS — application (L7 flood)** | Per-IP request rate (20/s, burst 40) + per-consumer token bucket (600/300/10 per min); honest 429s | APISIX `limit-req` + `limit-count` (global_rules + per-route) | test_apisix_config.py |
| **Slowloris / socket exhaustion** | Connection cap per IP (100 conn, 50 burst); upstream read timeouts | APISIX `limit-conn`; Go servers' timeouts | config gate |
| **Payload bombs** | 1MB body cap at gateway AND at facility edge | APISIX `client-control`; Caddy `request_body max_size 1MB` | config gates both tiers |
| **Bot/credential stuffing** | UA denylist (empty/scripted UAs) before auth work; tokens are short-lived client_credentials, no passwords to stuff | APISIX `ua-restriction`; Keycloak realm | config gate |
| **MITM — transport interception** | TLS floor 1.2, prefer 1.3 at edge and gateway; HSTS; no Server banner; internal CA for facility LANs with MDM-pushed roots (never skip-verify — benchmark harness uses CERT_NONE only against the internal CA) | Caddyfile `tls protocols`, header block; APISIX TLS | test_caddyfile.py |
| **MITM — downgrade** | TLS 1.2 floor kills protocol downgrade; HTTP→HTTPS not required on facility LANs by design (isolated LAN, internal CA) — the honest boundary | Caddyfile | config gate |
| **MITM — east-west (datacenter)** | WireGuard pod-to-pod encryption via Cilium; default-deny network | infra/k8s/cilium + hardening.yaml | k8s README |
| **Webhook forgery** | HMAC-signed webhook deliveries; consumers verify | notification-service (ADR-0061) | guide §6 |
| **QR forgery / replay** | HMAC keyring + rotation + revocation; presence codes daily-scoped; browser verification byte-compatible | ADR-0055 fabric | 39 PWA tests |
| **Supply chain** | CycloneDX SBOM + SHA-256 manifest per release; BSL/SSPL/Elastic licence gate fails the build; compose-don't-fork | tools/release/sbom.py | 4 tests |

### 2. Internal threats (the harder half)

| Threat | Control | Where | Evidence |
|---|---|---|---|
| **Mass record export (theft staging)** | secguard `mass-export` rule: >50 distinct patients/actor/day = critical finding | packages/secguard over SHR/consent audit lakes | 8 tests |
| **Off-hours bulk access** | `off-hours-bulk` rule (22:00–05:00) | secguard | tests |
| **Break-glass abuse** | `breakglass-burst` rule (>3/day); every break-glass already audited at SHR | secguard + shr-service audit | tests |
| **Privilege probing** | `scope-escalation` rule (≥10 × 403/day/actor) | secguard | tests |
| **Dormant account reactivation** | `dormant-sudden` rule (30d silence → bulk = critical) | secguard | tests |
| **Insider fraud (money)** | Double-entry ledger invariants, anti-collusion sampling, fraud ML | ADR-0003/0018/0023 | adjudicator suite |
| **Presence/attendance fraud** | Presence ladder, impossible-travel + scan-burst forensics | ADR-0055 | forensics tests |
| **Over-privileged staff** | Least-privilege scopes (Keycloak realm as code), step-up re-auth for sensitive actions, hrcred licensure gates on prescribing and QR keys | ADR-0008/0039, packages/hrcred | realm config + tests |
| **Tenant escape (multi-tenant SaaS)** | tenantguard CI gate on migrations + runtime row-isolation guard | packages/tenantguard | audits real migrations |
| **Consent bypass** | Federation and record access fail CLOSED when consent-service is down | shr-service federation | tests |

### 3. Doctrine rules that never bend

1. **Findings, not auto-bans** — insider detections produce auditor
   questions; auto-blocking a clinician can kill a patient. Severity ranks
   the queue.
2. **Fail-closed only for security/consent; fail-soft for care** — a dead
   Keycloak never blocks a ward; a missing consent always blocks a bundle.
3. **Every control is a config or a test** — posture that lives in a PDF
   decays; ours is gated by test_apisix_config.py, test_caddyfile.py,
   secguard exit codes, and the SBOM licence gate.
4. **Honest boundaries** — we do not claim L3 volumetric absorption (that's
   the provider/LB layer), and facility LANs trust the internal CA by
   design; both are written down, not glossed.

## Detection & response loop

1. APISIX + Caddy Prometheus metrics → plane Grafana (rate anomalies).
2. Audit lakes (SHR, consent, ledger) → nightly secguard run (exit 1 pages
   the integrity officer).
3. Wazuh on the fleet (k8s README) → host-level IDS.
4. Chaos runbook scenario cadence proves the fail-soft claims quarterly.
