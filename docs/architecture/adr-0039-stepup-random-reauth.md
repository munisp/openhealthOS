# ADR-0039: Step-up & random re-authentication at the PHI boundary

- Status: accepted
- Date: 2026-10-20
- Complements: ADR-0016 (portal record), ADR-0017 (passkeys/RPM),
  ADR-0038 (sovereign Matrix channel)

## Context

The portal gate (patient ID + phone, fail-closed) authenticates a *session*.
But a phone is a borrowable object: left on a table, borrowed by a child,
snatched in transit. Once a session exists, everything it could render —
lab values, imaging, medication lists, the consult thread — is clinical
detail (PHI), and the bar for *rendering PHI* must be higher than the bar
for *having a session*. NIST calls this step-up authentication; we add a
second, rarer property: **random re-authentication**, so a stolen session
degrades unpredictably instead of working until noticed.

## Decision

The portal rail splits endpoints into two classes:

- **open** — coverage, facilities, slots, feed metadata, booking (no
  clinical detail; the session gate suffices);
- **elevated** — `/portal/record` and any endpoint that renders clinical
  content. These additionally require a step-up token.

Step-up flow:

1. `POST /portal/stepup/challenge` {patient_id, phone} — pair checked
   fail-closed (404, no oracle); a 6-digit code is delivered over the
   patient's **own channel** — the provider chain: Matrix room when bound
   (ADR-0038 sovereign-first leg), SMS otherwise. The code is stored as a
   SHA-256 hash only, expires in 10 minutes, allows 5 attempts (then the
   challenge is destroyed), and burns on success.
2. `POST /portal/stepup/verify` — constant-time compare; success issues a
   random 256-bit elevated token (10-minute TTL, stored hashed).
3. Elevated endpoints require `X-StepUp-Token`; absence/expiry → **428
   `step_up_required`** (an app signal: "render the challenge screen"),
   never an identity hint.

Random re-authentication: every elevated read rolls the dice —
`STEPPUP_RANDOM_PERCENT` (default 15%) chance of 428 even with a valid
token, forcing a fresh challenge. An attacker holding a stolen session can
never *count on* the next read succeeding, and every bounce is a detection
opportunity.

Audit: every gate event (`challenge_sent`, `verify_failed`, `elevated`,
`blocked_no_elevation`, `blocked_random_reauth`, `phi_read`) is a
structured log line into the ops lake — challenges, failures, and bounces
are forensically visible without a new store.

## Consequences

- PHI rendering is gated by *fresh* proof, not by session age.
- The challenge rides the patient's strongest available channel — a bound
  howUdeyChat room (which itself required activation-time PIN) beats SMS.
- Random bounce rate is a deployment knob: high-sensitivity deployments
  raise it; the honest tradeoff is friction vs. stolen-session value.
- Passkeys (ADR-0017) remain the phishing-resistant upgrade path: the
  challenge/verify seam can carry a WebAuthn assertion instead of a code
  without changing the gate.
- Verified by `demo/matrix-stepup.sh`: 428 without elevation → code
  delivered into the patient's own Matrix room → wrong code 404 → elevate
  → replay burned → record renders → valid token randomly bounced.
