# ADR-0050: Integration map — Medplum spine, one event graph, all surfaces

Status: accepted (2026-10-20)

## Context

ADR-0041/0044–0049 added capabilities; this ADR records how they are
WIRED — into Medplum as the production FHIR core, into the event graph,
and onto every user surface (patient PWA, ops dashboards, howUdeyChat).
An unwired feature is shelfware; wiring is the feature.

## The event graph (who feeds whom)

```
Medplum FHIR (ADR-0010, production core)
  │ Subscriptions (rest-hook)                synthetic seed: synthseed --medplum
  ▼                                          (client-credentials, /fhir/R4 upsert)
notification-service /fhir/inbox
  │ MedicationRequest ──► dosagetext render (subprocess, fail-soft)
  │                       → prescription event carries dosage_text
  │                       → pickup message + adherence plan instruction
  │ MedicationDispense ─► adherence loop closes
  ▼
adherence tick: daily nudge carries the INSTRUCTION (not a bare reminder)
referral-service sweep: breach → POST /events {type:referral_breached}
  → notification-service: care-room escalation (HUC Matrix, fail-soft)
    + ops contacts — never a bare patient SMS for staff workflow events
facility-readiness: edge telemetry-collector readings → /readings
  → api-gateway /api/readiness → dashboards Readiness board (#/readiness)
portal timeline: notifications + telehealth consults + referral legs,
  all fail-soft, step-up gated (ADR-0039)
```

## Surfaces

- **Patient PWA** (`apps/patient`): timeline badges are i18n-keyed
  (`kind_*` across en/pcm/ha/yo/ig, `kind_consult` added); pickup
  messages carry the dosage instruction; cache bumped to v7.
- **Ops dashboards** (`apps/dashboards`): Readiness board — graded
  scores worst-first, honest UNKNOWN for silent sensors, demo set
  mirrors the live shape; cache bumped to v4.
- **howUdeyChat**: danger signs (WhatsApp gateway) and now referral
  breaches / adherence lapses (notification-service) escalate into
  care rooms via the same HUC admin path — one escalation doctrine,
  two entry points.
- **Android/iOS shells**: unchanged — they wrap the PWAs, so every
  improvement above ships to native automatically.

## Language doctrine check

All new code is Go (services, agentgate), Rust (unchanged hot paths),
stdlib Python (dosagetext, synthseed, schemainfer, fhirguard) or
TypeScript/JavaScript (PWA/dashboards). No JVM, no new runtimes.

## Consequences

- Compose brings up facility-readiness (:8093) and referral-service
  (:8094) on the platform plane; the gateway exposes both.
- Every cross-service leg is fail-soft with a completeness flag — the
  pattern is now uniform enough to be the contract, not the exception.
