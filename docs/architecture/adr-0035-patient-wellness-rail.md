# ADR-0035: Patient wellness rail — device data into the record, phone as prevention platform

Status: accepted · 2026-10-19

## Context

Patients carry their records on their phones (ADR-0016 portal PWA).
The same phone — plus watches, bands and clinic IoT — continuously
produces the data preventive medicine actually runs on: steps, heart
rate, sleep, workouts, weight, meals. Two requirements: (1) that data
must become part of the medical record with provenance, and (2) the
app must turn it back into prevention — a plan, due screenings,
achievable targets.

Honest constraints in Nigeria: most patients have no wearable, Android
dominates, connectivity is intermittent, and platform OAuth (Fitbit)
cannot be held by an offline-first edge.

## Decision

**`services/wellness-service`** — a small rail with an allow-list, a
journal, a mirror and a planner:

1. **Source allow-list, never deny-list** (same doctrine as wallets,
   ADR-0030): `manual`, `health-connect`, `apple-health`, `fitbit`,
   `generic-iot`. Unregistered sources are refused (403) before storage.
   Each source declares its honest path: Health Connect/HealthKit are
   read on-device by a companion sync; Fitbit arrives via Web API
   subscription webhooks (OAuth held by the companion, never the
   platform); generic-iot covers facility BP cuffs and scales bridged
   by a gateway app; manual is the zero-device baseline.

2. **Normalized ingest → FHIR Observations in the SHR.** Types map to
   LOINC where one exists (steps 55423-8, heart rate 8867-4, weight
   29463-7, BMI 39156-5) and OH-* local concepts otherwise (sleep,
   workout, nutrition) — the same concept namespace pophealth exports,
   so device data flows into OMOP-lite, cohorts, gaps and risk like any
   clinical observation. `performer: wellness:<source>` labels
   provenance; `source_facility: wellness-rail` marks the rail.

3. **Exactly-once and offline-first.** Client refs dedupe per
   patient+source; deterministic ids make replays idempotent; the local
   JSONL journal is the source of truth and SHR mirroring is
   best-effort (a failed mirror never fails an ingest).

4. **The plan sees both rails.** `GET /wellness/{p}/plan` merges the
   wellness journal with the patient's record shell and calls
   packages/pophealth (subprocess seam, same as benefits/economics):
   risk bands, overdue gaps, and lifestyle-aware recommendations (low
   step averages, missing workouts, unlogged nutrition nudges).

5. **Patient PWA wellness tab.** Prevention plan card (overdue items,
   risk with the heuristic label shown, recommendations), activity KPI
   grid, quick logs (workout/meal/weight/steps) with an offline outbox
   in localStorage that drains on reconnect via the same deterministic
   refs, and a connected-devices panel that is honest about how each
   source arrives. English + Nigerian Pidgin strings.

Gateway route `/api/wellness` → wellness-service.

## Consequences

- A patient with only a phone gets manual logging and the prevention
  plan; a patient with a Fitbit gets automatic steps/HR in their SHR
  record — same rail, same record.
- Clinicians see lifestyle data in the record with source labels, not
  mixed invisibly with clinical measurements.
- Care-gap closure can be nudged from the app; gap rules stay data.
- No third-party SDK or credential is embedded; companion syncs and
  webhooks are network-composed (compose-don't-fork, ADR-0005).

## Verification

wellness-service Go tests (ingest, exactly-once replay incl. after
restart, allow-list 403, type validation, mirror-failure locality,
FHIR mapping, summary windows, HTTP routes); patient PWA node tests
(wellnessKpis, planItems, riskBadge, deterministic refs);
`demo/pophealth.sh` steps 3–8: device ingest → dedupe → 403 →
Observations visible in SHR `$everything` with provenance → summary →
plan recommendations reflecting the step data.
