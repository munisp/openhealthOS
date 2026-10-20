# ADR-0055: QR operational hardening — key rotation, member cards, presence proof, scan forensics, payload standards

Status: accepted (2026-10-20)

## Context

An audit of QR usage found the cryptographic doctrine sound (offline
verification, fail-closed, tamper-evidence — OHIPS1 ed25519, OHAST1 HMAC)
but five operational gaps:

1. **No key rotation or revocation** — one HMAC secret, one ed25519
   keypair; a leak silently trusts every outstanding tag.
2. **No member-identity QR** — eligibility checks needed the network or
   a typed ID; a CHW offline could not verify coverage.
3. **No cheap presence proof** — the linkage gate ranks biometric > card
   > self; biometric hardware is rare at PHCs and cards get shared.
4. **No scan forensics** — custody scans were recorded but never
   analyzed for physically impossible patterns.
5. **No payload/density standards** — dense QRs fail on the low-end
   cameras our users actually carry.

## Decision

### 1. `packages/qrtrust` — shared keyring + revocation + budget (12 tests)

- Keyring env `OH_QR_KEYS` (JSON kid→secret) + `OH_QR_ACTIVE`; legacy
  single-secret envs degrade to a one-key ring under `k0` — never to
  "no key". Fail-closed when no usable key or active kid missing.
- v2 payloads carry `<kid>` as the second field; old keys stay in the
  ring so field tags verify after rotation; only the active key mints.
- Revocation JSONL of payload bodies → hard reject (`RevokedError`,
  distinct from bad signature).
- Payload budget: 800B target / 2000B hard ceiling, EC level Q; minters
  refuse beyond the ceiling (a QR that won't scan is a decoration).

### 2. `packages/membercard` — signed offline member QR (7 tests)

`OHMEM1.<kid>.<member>.<scheme>.<valid_yyyymmdd>.<hmac>` — a CHW's PWA
verifies authenticity, identity and validity with zero connectivity.
Expired is informational (renew), never a silent pass. Benefit details
stay in the benefits package — the card says *who*, not *what's owed*.

### 3. `packages/presenceqr` — facility-day presence QR (6 tests)

`OHPRS1.<kid>.<facility>.<yyyymmdd>.<hmac>` — a daily front-desk code the
patient's own authenticated phone scans on arrival. Anti-replay: codes
rotate daily, and yesterday's photographed code is rejected as a replay.
Scan events feed the encounter store as `presence: "qr-day"`, ranked
with card presence in the adjudicator's `PRESENCE_RANK` (clears the
sampling-review threshold). Device-bound: it's the phone logged into the
patient app that must be physically present.

### 4. `packages/assettag/forensics.py` — scan-pattern anomalies (10 tests)

Three detectors over custody JSONL: **impossible travel** (same asset,
two facilities, within the window — one scan saw a photocopy), **scan
bursts** (>10 scans/min by one actor — human sweeps have rhythm, replayed
tag sheets don't), **stale scans** (re-label programme health). Findings
are auditor questions, never accusations; findings exit non-zero for
CI/audit hooks.

### 5. Go mirror + standards

- equipment-service: `OHAST2` keyring mint/verify with revocation
  (mtime-cached), `VerifyTagAny` accepting v1 legacy tags so deployed
  stickers survive rotation day; the tag route mints v2 when the keyring
  is configured, v1 otherwise (2 new tests).
- `docs/qr-standards.md` — payload budgets, EC-Q, quiet zones, print
  guidance, and the honest statement of what a scan does and doesn't
  prove.

## Consequences

- Key compromise becomes a rotation, not a reprint-everything crisis;
  a leaked print sheet becomes a revocation line.
- Offline eligibility verification and presence proof close two real
  workflow gaps with payload formats, not new crypto — all reuse the
  existing HMAC/keyring seam.
- Custody data now generates alarms, not just evidence.
- Limitations (honest): membercard/presenceqr are Python CLIs — PWA-side
  JS verification (WebCrypto HMAC) is the next iteration for true
  zero-roundtrip offline scanning in the browser; presence QR proves the
  *phone* was there, and collusion (lending a logged-in phone) remains a
  sampling-audit question, as designed.
