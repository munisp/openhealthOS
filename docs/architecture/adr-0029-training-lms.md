# ADR-0029: Training and CPD — the platform's own lean LMS

Status: accepted (implemented)
Deciders: platform architecture

## Context

Quality, standardisation, certification, and continuous education for
nurses, technicians, doctors, and paramedics require LMS features.
General LMS products do not fit:

- **Moodle is GPLv3, Open edX is AGPLv3** — embedding either violates
  the licence doctrine; running either as a composed container drags in
  a PHP/Python monolith, its own database, and its own auth, none of
  which understand facilities, practitioners, guidelines, or the ledger.
- What a health system actually needs is smaller and stranger than a
  general LMS: SOP-versioned courses, regulator-auditable CPD points,
  assessor sign-offs, revocable certificates, and feature-phone access.

## Decision

Build the platform's own lean LMS: `services/training-service` (Go,
:8097) + a PWA page (`apps/pwa/training.html`) + an IVR seam.

### Model

- **Course** — id, title, content `version`, `guideline_version` (the
  clinical SOP the course attests to), `cpd_points`, `scheme`
  (MDCN for doctors, NMCN/MCPDP for nurses and midwives, NEMTC for
  paramedics, CHPRBN for community health practitioners), modules.
- **Module** — `video` (streams from the ADR-0028 video rail),
  `document` (SOP reads parsed by docintel), `quiz` (auto-graded against
  `pass_mark`), `checklist` (practical skills, assessor-gated).
- **Enrolment** — practitioner × course version, per-module progress.

### Certificates are ledger transactions

A certificate is a financial-grade fact, not a PDF. Issuance posts a
double-entry transaction on the ledger seam
(`system:cpd:issuance → cpd:<practitioner>`, amount = CPD points,
tx = `cert:<serial>`). The serial is a deterministic SHA-256 over
(course, version, practitioner), so issuance is idempotent.
**Verification** is a balance/journal query (`GET
/certificates/{serial}/verify`); **revocation** is a counter-entry
(`revoke:<serial>`), never a delete — the audit trail survives.
Regulators verify offline against a replicated ledger copy.

### Checklists reuse the WHO-gate doctrine

Practical sign-off mirrors the theatre WHO surgical-gate pattern: a
named assessor (X-Actor) signs each item; the module completes only when
every item is signed. Missing assessor → 422.

### Guideline bumps force re-attestation

`POST /guidelines/{course}/bump` records a new SOP version and flags
every existing enrolment `re_attest_required`. `GET
/facilities/{id}/attestation-gap` gives the facility manager the list of
practitioners whose training no longer matches current practice — the
quality-assurance loop closes.

### Feature phones are first-class

`GET /ivr/quiz/{enrolment}/{module}` exposes a quiz in the voicebridge
converse shape (prompts + numeric answers), so CPD works over IVR with
no data plan. The PWA page streams course video via native HLS /
lazy-loaded hls.js and keeps the certificate wallet readable offline.

## Alternatives considered

| Option | Why rejected |
|---|---|
| Embed Moodle | GPLv3; monolith; no ledger/facility awareness |
| Embed Open edX | AGPLv3; heavy; same structural mismatch |
| Compose Moodle as a container | Auth/DB duplication, no guideline versioning, no IVR, no ledger-signed certificates — the integration surface costs more than the LMS core |
| PDF certificates signed by a service key | No append-only trail; revocation unverifiable offline |

## Consequences

- CPD points are auditable by MDCN/NMCN with nothing but a ledger
  replica — a trust primitive no external LMS offers.
- The LMS reuses existing rails (video, docintel, voicebridge, theatre
  gate) instead of importing a second platform.
- What we deliberately do NOT build: discussion forums, SCORM, gradebook
  analytics. If a partner needs full pedagogy tooling, H5P (MIT) content
  can be embedded in course modules without licence conflict.
