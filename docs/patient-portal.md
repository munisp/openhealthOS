# The patient portal — and how it compares with Epic MyChart

The patient-facing tier of OpenHealth OS (ADR-0016) is a PWA at
`/patient/` — no app store, no install prompt, opens on a $100 Android
phone on 2G. It has two tabs:

- **Updates** — the notification rail: appointments, results-ready,
  medication pickup, referrals, immunisation reminders. Everything here
  also arrives by SMS; a feature phone gets the same events by
  SMS/USSD.
- **My record** — the patient's own FHIR record on the patient's own
  authority (NDPA data-subject access): clinic visits, test results,
  medicines, immunisations, conditions, allergies, and the telehealth
  consult thread. Cached on the phone with an honest "last updated"
  stamp; a partially-reachable platform shows a banner, never a fake
  "complete".

Identity is the patient code + registered phone (bound to the national
KYC identity, ADR-0012). Language is English, Naija (Pidgin), Hausa,
Yorùbá, or Igbo, switchable in one tap.

## Side-by-side with Epic MyChart

| Capability | Epic MyChart | OpenHealth patient app |
|---|---|---|
| Test results | ✅ full result detail, trends, clinician comments | ✅ results with values, units, interpretation flags |
| Visit history / summaries | ✅ after-visit summaries, notes (open notes) | ✅ visits with reason and facility |
| Medications | ✅ list, refill requests | ✅ list with dose instructions (refill request: not yet) |
| Immunisations | ✅ | ✅ |
| Conditions / allergies | ✅ | ✅ |
| Care-team messaging | ✅ in-app messaging with the clinic | ❌ not yet (SMS rail is one-way) |
| Appointment scheduling | ✅ self-scheduling, wait lists, e-check-in | 🟡 appointments arrive as updates; self-scheduling not yet |
| Video visits | ✅ in-app telehealth launch | ✅ telehealth thread visible; visit itself runs on the telehealth tier |
| Bill pay / insurance | ✅ statements, estimates, payment plans | ❌ not applicable yet — claims run facility-side (NHIS/HMO), no patient billing tier |
| Proxy (family) access | ✅ parent/caregiver views with granular rules | ❌ not yet — one subscription, one patient |
| Cross-organisation record | 🟡 MyChart Central/Lucy — Epic sites only, US-centric | ✅ every facility on the platform writes one national record |
| Works offline | ❌ live view only | ✅ full last-known record cached, honestly stamped |
| Works on 2G / $100 Android | 🟡 heavy web app / native app | ✅ PWA, KB-scale payloads, no WebGL, no app store |
| Feature-phone fallback | ❌ | ✅ same events over SMS/USSD |
| Languages | 🟡 English/Spanish-first, org-dependent | ✅ 5 Nigerian languages, plain-language voice |
| Record portability | 🟡 Share Everywhere (to Epic partners) | ✅ IPS QR — any FHIR-IPS reader, any country |
| Identity assurance | 🟡 org-issued account, varies | ✅ national KYC-bound patient code + phone |
| Availability precondition | an Epic customer hospital | any facility on the platform — PHC upward |

## Where MyChart is ahead — honestly

MyChart is a mature patient-engagement product. It leads on the
interactive tier: two-way care-team messaging, self-scheduling with
e-check-in, bill pay and estimates, proxy access for parents and
carers, refill requests, questionnaires, and wearable data. None of
these is doctrinally excluded from OpenHealth; they are sequenced work
(messaging and proxy access are the natural next ADRs), and some — bill
pay — only become relevant when a patient-billing tier exists at all.

## Where OpenHealth is structurally different

MyChart is a window into *one health system's* Epic instance, online.
OpenHealth's patient app is the read tier of *national infrastructure*:

1. **Offline-first, not online-only.** The record is a local copy the
   phone owns, refreshed when the network allows — because the network
   often doesn't allow. MyChart offline is a login error.
2. **Not hospital-locked.** Aggregation isn't a federation feature
   (Lucy/Central) — it's the base case. A patient seen at a PHC in
   Kaduna and a general hospital in Lagos has one record, because both
   write to the same platform spine.
3. **The app is the rich tier of a multi-tier channel.** The same
   events reach a feature phone by SMS/USSD. MyChart has no fallback
   tier; the digitally excluded simply don't have MyChart.
4. **Portability by open standard.** The IPS QR travels to any
   FHIR-IPS reader anywhere — not only to partners of one vendor.

The short version: **MyChart is deeper inside the hospital; OpenHealth
is wider across the country and reachable from the bottom of the
network.** For the population OpenHealth serves — PHC-first,
connectivity-poor, multi-language — the trade-offs chosen here are the
right ones, and the gaps that remain are named, scheduled, and
doctrine-compatible.
