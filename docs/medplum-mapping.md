# Medplum capability mapping (ADR-0017)

How the top Medplum solution areas (medplum.com/solutions) map onto
OpenHealth OS after the ADR-0017 wave — what exists, where it lives,
and how it is proven. "Different on purpose" marks places where the
Nigerian offline-first constraint deliberately beats the Medplum shape.

| # | Medplum capability | OpenHealth OS answer | Proof |
|---|--------------------|----------------------|-------|
| 1 | **Scheduling / self-booking** | `scheduling-service`: weekly templates → deterministic slots, atomic booking, FIFO waitlist, reschedule; patient self-books via `/portal/appointments` | `demo/scheduling.sh` (no-double-book 409, waitlist promotion) |
| 2 | **Eligibility & coverage verification** | Adjudicator enrolment store (`MEMBERS_FILE`) + `/enrolments` API; eligibility says WHY (suspended/expired/unknown) with the route back to care; advisory snapshot at booking | `demo/eligibility.sh` steps 1–3 |
| 3 | **Care-team messaging / triaged inbox** | Ask-the-clinic (`/portal/messages`) opens a triaged telehealth consult (`portal-triage` identity) the facility's nurse pool picks up | `portal_actions_test.go` |
| 4 | **Questionnaires / screening forms** | Bundled FHIR Questionnaire (ANC danger-sign screening) rendered on-device in 5 languages; answers become a QuestionnaireResponse in the SHR; danger signs say "come in TODAY" | `questionnaire.test.mjs` |
| 5 | **Care plans / next-due** | Patient app next-due card computed from upcoming appointments; immunisation due reminders ride the SMS rail (`immunization_due`) | `care.test.mjs` |
| 6 | **Remote patient monitoring / device data** | Danger-band vitals alerting via `/fhir/inbox`; bands are data (`VITALS_RULES_FILE`), in-band silence, patient-safe labels only over SMS | `vitals_rules_test.go` |
| 7 | **Workflow automation (bots)** | The event rail: booking → SMS, cancel → waitlist SMS, discharge → SHR write-back + SMS, lab result → pickup message. Events are the automation substrate; a general bot runner is a deliberate non-goal (typed handlers over arbitrary code) | `renderMessage` + e2e demos |
| 8 | **Documents** | DocumentReference rows in My record (discharge summaries land here automatically from IPD discharge) | `care.test.mjs` |
| 9 | **Consent management** | `/portal/consents` list + ownership-checked revoke; consent-service artefacts are signed, every check audited | `portal_actions_test.go` |
| 10 | **Provider directory** | Practitioner FTS (`/providers/search`) on the same stored-tsvector + GIN answer as facilities/patients — no search cluster | `search_test.go` |

## Look and feel

The patient app's Care tab adopts the Medplum visual grammar — clean
white cards, soft shadows, pill status badges, indigo accent — inside
the existing calm daylight theme, at WCAG AA contrast (enforced by
`tools/a11y-contrast.py`).

## Different on purpose

- **Offline-first over cloud-first**: the portal works from cached
  copies with honest staleness banners; Medplum assumes connectivity.
- **Feature-phone tier**: every self-service action has an SMS
  counterpart — Medplum has no USSD/SMS tier.
- **Anti-gaming identity**: registry biometrics with a 1:N dedupe gate
  and a KYC ladder — Medplum has no biometric layer.
- **No vendor SDKs**: Medplum is itself the vendor; every third-party
  seam here is a clean-room interface (ADR-0005).
