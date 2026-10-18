# Hospital operations: gap analysis and what ADR-0017 closed

An honest audit of OpenHealth OS against what a Nigerian general
hospital needs to run — what existed, what was missing, what shipped,
and what is sequenced next.

## Before ADR-0017: outpatient-only

The platform was deliberately PHC-first: registry, SHR, telehealth,
pharmacy, claims, notifications, sync. A general hospital could not run
on it — there was no answer to "which bed is this patient in?" and no
answer to "when is their appointment?"

## Gap table

| Hospital capability | Before | After ADR-0017 | Where |
|---------------------|--------|----------------|-------|
| Appointment scheduling | ✗ nothing | ✓ engine + patient self-booking + waitlist | `scheduling-service` |
| Theatre / room booking | ✗ | ✓ same engine (`resource_type: room|equipment`) | `scheduling-service` |
| Ward / bed board | ✗ | ✓ wards, beds, `free→occupied→cleaning→free` | `ipd-service` |
| ADT (admit–transfer–discharge) | ✗ | ✓ atomic transfer, one-open-admission invariant (code + partial unique index) | `ipd-service` |
| Discharge summary into record | ✗ | ✓ automatic: Encounter (IMP) + DocumentReference to SHR, visible in the patient app | `ipd-service` write-back |
| Coverage / eligibility check | partial (claims-only) | ✓ enrolment registry, reasons-why, advisory at booking | adjudicator + `/enrolments` |
| Identity assurance / anti-fraud | NIN + phone OTP | ✓ + biometric dedupe (409 on second identity) and ladder top `biometric-verified` | registry biometrics |
| Inpatient charting (notes, drug charts) | ✗ | ✗ sequenced | next vertical slice |
| Surgical safety workflows | ✗ | ✗ sequenced (rooms book; checklists are workflow) | — |
| Bed occupancy analytics | ✗ | ✗ data now exists; ML slice next | — |
| Emergency/triage board | ✗ | ✗ sequenced | — |
| Blood bank / transfusion | ✗ | ✗ sequenced | — |
| Mortuary | ✗ | ✗ sequenced | — |

## What "end to end" means for the ADT slice

Admit a patient from the ward desk → the bed flips occupied → transfer
to another ward is one atomic operation (never stranding the patient) →
discharge frees the bed into cleaning *and* writes the stay back to the
shared record *and* messages the patient's phone → the patient's own
app shows the inpatient encounter and the discharge summary under
Documents. Housekeeping returns the cleaned bed. Every step is
invariant-checked, audited, and dual-store (memory edge / Postgres
state plane).

## Sequenced next (honest)

1. **Inpatient charting** — progress notes and drug charts on the
   admission; the ADT backbone is in place for them to hang on.
2. **Emergency triage board** — arrival stream + acuity, feeding the
   same bed board.
3. **Surgical safety** — WHO checklist as a workflow gate on
   theatre-slot check-in (the scheduling engine already books the room).
4. **Occupancy analytics** — the ML slice consumes the admission ledger.
