# Platform roles and onboarding workflows

Every action on OpenHealth OS is attributed to a person with a role.
This document is the catalogue; the enforcement lives in
`services/registry-service/onboarding.go` (the role → required-steps
matrix is data, not code, so a state can extend it without a fork).

## The roles

| Role | Cadre code | What they do on the platform | Sensitivity |
|---|---|---|---|
| Patient | `patient` | Own record, booking, coverage, disputes, SOS | self |
| Community Health Worker (CHEW/CHW) | `chew` | Community triage, household visits, referrals, immunisation follow-up | patient data (assigned catchment) |
| Nurse | `nurse` | Vitals, triage, care plans, medication admin | patient data (facility) |
| Midwife | `midwife` | ANC, delivery, postnatal care | patient data (facility) |
| Physician / CHO | `physician` | Diagnoses, prescriptions, admissions, discharge | patient data (facility) |
| Pharmacist | `pharmacist` | Dispensing, stock counts, cold chain | patient meds + stock |
| Lab scientist | `lab-scientist` | Orders, results, notifiable-disease lab confirmations | patient results |
| Facility admin | `facility-admin` | Facility profile, staff roster, tariffs, hours | facility config |
| Dispatcher | `dispatcher` | Emergency intake, assignment, case ownership (ADR-0019) | live emergencies |
| Paramedic | `paramedic` | On-scene updates, transport status | incident data |
| LGA officer | `lga-officer` | Quality scorecards, surveillance rollup, stock reconciliation | aggregates only |
| State program officer | `state-program-officer` | Measure library, program targets, alerts | aggregates only |
| Payer claims officer | `claims-officer` | Claim adjudication, disputes, PEND review | claims (no clinical detail) |
| Auditor | `auditor` | Hash-chained audit, integrity scorecards, dispute evidence | read-only, everything attributed |

## Onboarding workflow (all staff roles)

```
invite → identity-check → credential-verify → facility-assign → activate
```

1. **invite** — a `facility-admin` (or LGA officer for roving roles)
   creates the onboarding record; the invite names the role, the
   invitee's phone, and the inviter. Anonymous onboarding is refused —
   the same named-actor rule as enrolment (ADR-0018).
2. **identity-check** — NIN verification via the NIMC adapter seam;
   rank follows the KYC ladder and never downgrades.
3. **credential-verify** — licence checked against the professional
   council via `tools/provcred` (MDCN, Nursing & Midwifery Council,
   PCN, MLSCN). Roles without a statutory licence (dispatcher,
   facility admin, LGA officer…) skip this step — the matrix says so.
4. **facility-assign** — the role is bound to a facility (or LGA/state
   scope for officers); this is where the ReBAC bootstrap grants the
   role's default relations.
5. **activate** — the account goes live; the whole chain is on the
   onboarding record's timeline with actors and timestamps.

Suspension is a first-class state (`suspend` / `reinstate`) — it says
why, names who, and never deletes the record.

## Role → required steps matrix

Defined in code as `RoleMatrix` (data): each role lists which of the
five steps are required. Examples:

- `physician`, `nurse`, `midwife`, `pharmacist`, `lab-scientist`:
  all five steps (licence is statutory).
- `chew`: all five (state CHEW licence).
- `dispatcher`, `paramedic`, `facility-admin`, `lga-officer`,
  `state-program-officer`, `claims-officer`, `auditor`:
  no credential-verify; identity + assignment still required.
- `patient`: self-registration with phone verification (KYC ladder
  handles upgrades; see ADR-0017) — patients are not onboarded by
  staff.
