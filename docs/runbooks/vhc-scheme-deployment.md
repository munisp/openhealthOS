# Runbook: VHC scheme deployment (visitor health cover)

How to stand up the VHC stack for one country. The engine is
country-agnostic — **everything national lives in a versioned profile
entry** (`VHC_PROFILES`, `CLAIMS_RULES`, `FUND_RULES`, tariff book). If
you find yourself writing `if country ==`, stop: add config.

## 0. What exists vs what you deploy

The repo ships the *engine*: cover sales (block / flat+per-day pricing,
age bands, extensions), signed offline QR + green/amber/red verdicts,
exemptions, desk service, claims (intake → GOP → dossier → adjudication
→ payment run → appeal), medevac/remains, uninsured fund, subrogation,
provider accreditation + tariff book, incident registry + BR-10,
five-bucket fund + backstop, settlement/reconciliation, manifests,
partner doors, helpdesk, content, marts. `vhcover@1.0.0` …
`vhc-support@1.0.0`; e2e beats 08:42–08:58; 38 seams proven.

You deploy: profiles, keys, rails, lanes, and people. Neither half is
optional. **A cable not plugged in is not an interface.**

## 1. Country profile

Add a profile entry (see `vhcover.VHC_PROFILES`):

| Key | Notes |
|---|---|
| `model` | `block` or `flat_per_day` |
| `block_days` / prices | minor units of `currency` |
| `age_bands` | ordered; first match wins; `max_age: None` catches adults |
| `alt_currency` | display courtesy only, stated rate — never a second ledger |
| `grace_days`, `expiring_soon_days`, `humanitarian_days` | verdict-matrix thresholds |
| `qr_offline_days`, `cache_max_age_days` | offline validity; cache staleness budget |
| `exemption_categories`, `transit_max_days`, `foreign_policy_floor_minor` | the §5.2-style decision table |
| `max_covered_days` | beyond it is residence, not tourism |

Bucket allocations must sum to 100 or the table refuses to load.
A profile change is a version bump, never an edit in place.

## 2. Key ceremony (QR signing)

1. Generate the signing key inside the HSM. Register via
   `KeyRing.register(key_id, signer=…, verifier=…, active_from=…)` —
   HSM-bound keys expose sign/verify closures only; `snapshot()` refuses
   for them (**an HSM does not ride in a field kit**).
2. Distribute the *verification* key ring + revocation snapshot to lanes
   via `offline_cache(as_of=…)`; refresh inside `cache_max_age_days`.
3. Rotation: register the successor key with `active_from`, set
   `active_to` on the predecessor. Old tokens verify inside their
   window, then honestly refuse. Keep at least one overlap window per
   `qr_offline_days`.

## 3. Money rails

- PSP descriptors are composed (Stripe/Paystack/Flutterwave/Adyen
  shapes); wire the transport, never vendor an SDK. Card-not-present
  without 3DS refuses. The PAN never lands.
- Settlement: run `settlement_file(txns, day=…, psp=…)` per PSP per day;
  `reconcile(...)` against the PSP's file. Break tolerance is config
  (`break_tolerance_ppm`, default 1000 ppm = 0.1%). Past tolerance →
  escalate with the file; the breach names itself.
- One currency per settlement file — mixed-currency sums refuse.
- Fund: `post_premium` splits at posting time (seam 22 wires purchase →
  allocation, idempotent). Bucket overdrafts refuse; report the deficit.

## 4. Border lanes

- Lane kit: the verification PWA (`apps/pwa/vhc-verify.html`) + cached
  key ring + revocation snapshot, refreshed inside the cache budget.
  Verdicts are green/amber/red with named reasons and officer actions;
  every verdict carries measured latency against the 2s budget.
- A stale cache is a RED ("verification data stale — escalate"), never
  a shrug. Silence is a finding.
- Pre-clearance: airlines submit manifests; per-passenger statuses ride
  back (cleared / cleared-exempt / not-cleared) — names never ride back.
- The desk: `desk_lookup` (passport+dob), `reissue`, humanitarian flags
  (72h regularisation), exemption registration with evidence refs.

## 5. EASE-style national rail (optional but expected)

Composed adapter (`vhease`): `cover_issued`, `exemption_issued`,
`verification_event`, `manifest_batch`. The transport is injected;
`EaseAdapter(None)` refuses to construct. Point it at the national
single-window endpoint; contract-test against their sandbox before
go-live.

## 6. Claims operations

- Intake channels are config (`phone/portal/whatsapp/kiosk`); the GOP
  clock is 2h from intake and breaches are recorded, not excused.
- Tariff book: propose → **four-eyes approve** → effective-dated lookup.
  Unknown act codes route to manual review, never an invented price.
- Payment runs carry remittance advice; appeals resolve inside 5 days.
- Medevac dispatch SLA 2h from request; repatriation of remains requires
  mortuary + consulate references. Both, always.
- The uninsured fund: post with provider + reason; recoveries bounded;
  subrogation recoveries post back into the fund.
- Backstop: activation requires who + why + regulator notice reference;
  the fund keeps paying in backstop; exit requires solvency evidence.

## 7. Registry & surveillance

- Incidents register pseudonymized (salted hash; the salt is
  mandatory). Versioned vocabularies for syndromic class and outcome.
- BR-10 evaluation runs daily: ≥5 GI cases, same facility, same week →
  alert. Duty officer acknowledges with name + action; the report ships
  response-day clocks.
- Export packs (IHR/UKHSA/ECDC): aggregates only, k-anonymity floor 5,
  suppressed cells stated, sha256 on every pack.

## 8. Day-1 checklist

- [ ] Profile entry reviewed and versioned; buckets sum to 100
- [ ] HSM key ceremony done; rotation window agreed; cache distribution
      scheduled inside `cache_max_age_days`
- [ ] PSP contract test: charge, 3DS, decline, refund, settlement file,
      reconciliation clean
- [ ] EASE rail contract test green (or the rail honestly unplugged)
- [ ] Lane kit spot-check: green/amber/red paths, stale-cache red,
      offline mode
- [ ] Claims dry run: intake → GOP inside 2h → dossier → adjudication
      → payment run → remittance → appeal
- [ ] BR-10 tabletop: five synthetic GI incidents, alert, ack, export
      pack checksum verified
- [ ] Backstop tabletop: activation with notice ref, claims still pay,
      deactivation with solvency evidence
- [ ] Content coverage report per language; fallbacks stated
- [ ] `python3 -m pytest packages -q` green on the deployment tag

## 9. Ops clocks (who watches what)

| Clock | Budget | Owner |
|---|---|---|
| GOP issuance | 2h from intake | assistance ops |
| Adjudication | 10d from dossier | claims |
| Payment run | 30d from adjudication | finance |
| Appeal resolution | 5d | medical director |
| Medevac dispatch | 2h from request | assistance ops |
| Helpdesk first response / resolution | 4h / 24h | helpdesk lead |
| Settlement recon | daily, ≤1000 ppm breaks | finance ops |
| Cache refresh | inside `cache_max_age_days` | border IT |
| BR-10 evaluation | daily | surveillance duty officer |

Every breach is recorded with its denominator in the module's report —
the dashboards read the reports, not vibes.
