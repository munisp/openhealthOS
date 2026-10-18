# ADR-0016: The patient's own record, on the patient's own phone

Status: Accepted
Date: 2026-10-18

## Context

Until this ADR the patient app was a notification rail: appointments,
results-ready pings, pickup notices, referrals. The honest answer to
"does every patient have their medical records, labs, pharmacy,
telehealth and other healthcare information on their mobile phone?" was
**no** — the phone carried news *about* the record, never the record.

This ADR closes that gap. The patient app now carries the patient's own
FHIR record (visits, lab results, medicines, immunisations, conditions,
allergies) and the telehealth consult thread, fetched on the patient's
own authority, cached offline, in the five product languages.

The constraints that shaped every decision below:

1. **The devices are $100 Android phones on 2G.** The record must be
   readable with zero connectivity and must never pretend a stale copy
   is live.
2. **The patient is not a clinician.** Plain-language section names,
   values with units, no raw FHIR, no codes without displays.
3. **Self-access is a legal basis, not a consent grant.** NDPA 2023
   data-subject access: the requester IS the subject. But it must be
   authenticated, audited, and impossible to confuse with clinical
   access.
4. **A dark service must never 500 a patient's phone.** Nigeria-scale
   degradation doctrine applies to the portal too.

## Decision 1 — Self-access is its own audited basis: `patient-self-access`

The SHR gains `PatientSelfEverything(patientID, actor)`. It does not
consult the consent checker — a patient needs no consent artefact to
read their own record — but it writes the same audit entry every other
read writes, with `basis=patient-self-access` and the portal named as
actor (`portal:<patient_id>`). A test proves it works even with the
consent service down: self-access is not a consent bypass, it is a
different legal basis entirely.

The HTTP seam is the `X-Patient-Self` header on
`GET /fhir/Patient/{id}/$everything`. The header must match the
requested patient exactly — anything else is a 400, never trusted.
The SHR trusts this header only because its ingress is
NetworkPolicy-locked to platform services; no external caller can reach
it. This is the same trust posture as the ReBAC gate (ADR-0015):
assertion headers are honoured exclusively inside the platform
perimeter.

## Decision 2 — The notification service is the portal proxy

The phone already authenticates against the notification service for the
feed (patient code + registered phone → subscription store, 404 on any
mismatch — no enumeration). The record rides the same gate:
`GET /portal/record?patient_id&phone` fans out to two upstreams on the
patient's behalf:

- **SHR** `$everything` with `X-Patient-Self: <id>` — the clinical
  record;
- **telehealth** `GET /consults?patient_id=` — the consult thread,
  oldest-first (new endpoint on the same nurse-first consult store;
  memory and Postgres implementations, parity-tested).

Why the notification service and not the SHR directly: the gateway
already routes `/api/notify/*` to it, the subscription gate already
lives there, and the patient app has exactly one backend to trust. The
SHR stays a clinical system that never sees patient credentials.

## Decision 3 — Degradation is per-leg, explicit, and honest

Each upstream leg has its own 4-second timeout. A failed leg returns an
explicit `null` in the merged payload and flips `complete` to `false`;
the healthy legs still serve. A 404 from the SHR is **not** a failed
leg — "this patient has no record yet" is the truth, rendered as an
honest empty bundle, not an outage.

The app mirrors the doctrine: the last good record is cached in
localStorage exactly like the feed. Offline, the patient sees their
saved copy with a "Last updated" stamp; a partially-fetched record
carries an inline banner ("Some parts could not load right now —
showing what we have"). The phone never fabricates completeness.

## Decision 4 — Plain-language sections, server stays FHIR

The proxy ships the raw FHIR bundle untouched; the phone normalises it
(`groupRecord` in `apps/patient/js/logic.js`, pure and node-tested)
into the sections a patient actually asks about: clinic visits, test
results, medicines, immunisations, conditions, allergies, and
phone-and-video consults. Rows carry a title, a value (with units and
interpretation flags like "(Low)"), a relative time, and a source
facility. Defensive by construction: a half-written resource still
renders its truths, unknown resource types are skipped and counted.

FHIR stays at the service boundary — the phone is a rendering tier, not
an interoperability tier. The IPS QR portability path (ADR-0012) is how
the record leaves the platform; the app is how the patient lives with
it.

## Decision 5 — What this is not (the MyChart line)

`docs/patient-portal.md` carries the full Epic MyChart comparison. The
load-bearing differences recorded here:

- **OpenHealth is offline-first; MyChart is online-only.** Our record
  is a cached local copy, honestly stamped. MyChart is a live view into
  one health system's Epic instance and disappears with connectivity.
- **OpenHealth is not hospital-locked.** The record aggregates every
  facility on the platform (PHC, general hospital, telehealth, pharmacy)
  under the patient's national identity — MyChart shows one Epic
  customer's chart (MyChart Central / Lucy aggregate across Epic
  sites only).
- **OpenHealth degrades to SMS/USSD** for feature phones; the app is
  the rich tier, not the only tier.
- **What MyChart has that we do not (yet):** care-team messaging,
  self-scheduling, bill pay, proxy/family access, e-check-in, wearable
  integrations. These are sequenced gaps, not doctrine — messaging and
  proxy access are the natural ADR-0017 candidates.

## Verification

- Go: SHR self-access tests (allowed + audited; needs no consent —
  proven with a failing consent checker; unknown patient 404);
  telehealth thread tests (oldest-first, no cross-patient leak);
  portal proxy tests with stub upstreams (self header asserted, 404 =
  empty bundle, leg-down = explicit null + `complete=false`, consults
  unwrapped, auth gate = indistinguishable 404s).
- Node: `groupRecord`/`groupConsults` conformance (sections, sorting,
  units + interpretation, malformed-bundle tolerance).
- e2e: `demo/patient-record.sh` — three in-memory services, real HTTP:
  subscribe → seed FHIR → run a consult → merged fetch asserts bundle
  contents, audit basis, auth gate, and dark-SHR degradation.
- UI: gateway-served app driven end-to-end (sign-in → My record tab)
  in English and Hausa.

## Consequences

The answer to "can every patient have their record on their phone?"
becomes **yes, with one honest caveat**: the record on the phone is as
complete as the facilities that write to the platform. A facility that
is dark or not yet onboarded contributes nothing to the bundle — the
app says so (`complete=false`) rather than papering over it. Coverage
is an onboarding programme, not a software feature.
