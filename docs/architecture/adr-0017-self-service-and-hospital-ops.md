# ADR-0017: Patient self-service tier and hospital operations

**Status**: accepted (2026-10-18) · supersedes nothing; extends ADR-0011
(notification rail), ADR-0012 (identity/payers), ADR-0015 (hardening),
ADR-0016 (patient record portal).

## Context

The platform answered "can the patient *see* their care" (ADR-0016) but
not "can the patient *act* on it", and it answered primary care but not
the general hospital. The driving questions were concrete:

1. Can a patient schedule an appointment themselves? *(No — there was
   no scheduling engine at all.)*
2. How is coverage validated before care? *(Adjudication existed for
   claims, but there was no enrolment registry to check against — and
   no answer to "is THIS patient covered TODAY?")*
3. Can biometrics stop coverage fraud? *(Nothing existed.)*
4. How do the top Medplum capabilities map onto this platform, honestly?
5. What is missing for hospital management? *(Everything inpatient:
   no wards, no beds, no ADT.)*

## Decisions

### 1. Scheduling engine (new `scheduling-service`, port 8092)

Weekly **Schedule** templates (facility, resource, service, weekday,
window, slot size, capacity) materialise into deterministic **Slots**
(`scheduleID:date:startMin` — regeneration is idempotent, edge retries
are safe). Booking invariants live in one lock/transaction: no
double-book; reschedule takes the new slot *before* releasing the old;
cancellation promotes the FIFO waitlist head in the same transaction.
Resource type (`provider | room | equipment`) makes the same engine
cover theatre and equipment booking. Booking window: 30 days.

Patients self-book through the portal gate (`/portal/appointments`),
never directly — patient ids are not enumerable from the internet.

### 2. Eligibility: an enrolment registry with honest answers

The adjudicator gains a file-backed **enrolment store**
(`MEMBERS_FILE`, atomic writes). Schemes are validated against the
payer registry at enrolment — an unknown scheme is refused at the door.
Suspension requires a reason and never deletes history.
`check_eligibility` now says **why**: suspended (with reason), expired
(with date and the route back: "renew at your PHC"), unknown ("visit
your PHC to enrol"), plus `benefit_services` and `biometric_required`.
The orchestrator exposes `/enrolments` (enrol + suspend) over the same
subprocess seam as adjudication — one store, one truth.

Booking takes an **advisory** coverage snapshot: the answer decorates
the appointment, it never decides whether the appointment exists — a
patient is never refused a booking because an eligibility node is dark.

### 3. Biometrics: anti-gaming identity assurance (registry)

- A `Matcher` seam with a deterministic `SimMatcher` (normalised
  Hamming over template bytes). A licensed AFIS swaps in behind the
  interface — no proprietary biometric SDK is ever embedded (ADR-0005
  doctrine applies to biometrics too).
- Templates are **sealed at rest** (sealx); unprovisioned dev stores a
  loud `PLAIN:` sentinel, never a silent one.
- **The anti-gaming rule**: enrolment runs a 1:N identify first. A body
  already enrolled to another patient is a duplicate identity — refused
  (409), loudly. This stops the two real fraud shapes: double
  enrolment, and card/phone lending (the lender's finger isn't on the
  borrower's record).
- Verify is 1:1 and upgrades the KYC ladder to `biometric-verified`
  (rank 4, above `nin-verified`; the ladder never downgrades).
- One policy knob: `BIOMETRIC_THRESHOLD` (default 0.85).
- The NIMC sandbox speaks `/biometric/verify` so the authority-side
  biometric check is conformance-tested over real HTTP — swapping in
  the licensed NIMC service is configuration, not code.
- **Honest scope**: no liveness/PAD. That is a capture-device property
  (the scanner at the desk), not software.

### 4. Passkey unlock (`shared/webauthn`, stdlib-only ES256)

WebAuthn registration/assertion verification with a minimal in-repo
CBOR parser — no third-party WebAuthn library. Registration is gated
by the subscription phone check (the trust anchor); challenges are
single-use, 5-minute TTL; sign-count non-advancement is clone
detection and rejected. Attestation *trust* (vendor chain) is
intentionally out of scope — device provenance does not address our
fraud model; the registry-side biometric does.

### 5. The Medplum top-10, mapped (see docs/medplum-mapping.md)

Scheduling, eligibility, care-team messaging (ask-the-clinic → triaged
telehealth consult), questionnaires (bundled ANC screening →
QuestionnaireResponse), care plans/next-due (patient app), remote
patient monitoring (danger-band vitals via the FHIR inbox, data-driven
rules file), documents (DocumentReference in the record), consent
management (list/revoke in the app, ownership-checked), provider
directory (practitioner FTS), workflow automation (the event rail
itself: booking → SMS, discharge → record write-back).

### 6. Hospital operations: `ipd-service` (port 8093)

Wards, beds (`free → occupied → cleaning → free` — infection control is
a state, not a hope), and ADT. Invariants in one lock/transaction: one
open admission per patient; one patient per bed; **transfer is atomic**
(destination taken before source released — a failed transfer never
strands a patient). Discharge writes an inpatient `Encounter`
(class `IMP`) + `DocumentReference` discharge summary back to the SHR,
so the stay appears in the patient's own record on their own phone.
The one-open-admission invariant is also a partial unique index in
Postgres — the database guarantees it, not just the code.

### 7. Remote patient monitoring

Danger-band alerting on device-fed vital-signs Observations through the
existing `/fhir/inbox`. Bands are **data, not code**
(`VITALS_RULES_FILE`, JSON; embedded defaults track WHO/Nigeria
primary-care referral guidance). Inside the band: silence — the rail
never spams a patient for being healthy. Outside: an urgent come-in
message carrying a patient-safe label, never the raw number (SMS is not
a private channel). Unconfigured device codes can never page anyone.

## Consequences

- The portal gate pattern (subscription + phone, indistinguishable 404)
  is now the single trust boundary for every patient-facing action;
  domain services stay internal.
- Two new services, four new migrations (0010–0014: scheduling,
  biometrics, passkeys, IPD, provider FTS) — all memory/Postgres
  dual-store per the state-plane doctrine.
- Every claim in this ADR has an executable proof: unit tests per
  module, `demo/scheduling.sh`, `demo/eligibility.sh`, and the
  biometric conformance test over real HTTP.

## What this is NOT (honest gaps, sequenced)

- **Liveness/PAD** for biometrics — capture-device property.
- **Theatre-specific constraints** (anaesthesia sign-off chains,
  sterile-turnover timers) — the engine books rooms; surgical safety
  checklists are a workflow layer on top, not booked yet.
- **Ward-round clinical documentation** (progress notes, drug charts) —
  the IPD service is the ADT/bed backbone; inpatient charting is the
  next vertical slice.
- **Bed management analytics** (occupancy forecasting) — the data now
  exists; the ML slice consumes it next.
- **Attestation trust** for passkeys (see decision 4).
