# ADR-0011: National scale — LGA hub tier, notification rail, patient channels

**Status**: accepted · **Date**: 2026-10-18

## Context

ADR-0001/ADR-0009 solved the facility edge (offline-first PWA + sync
agent) and ADR-0006/ADR-0008 the state data plane. Between those two
tiers lies the hardest part of a Nigerian national rollout: thousands of
facilities per state uploading over intermittent 2G/3G, power cuts at
every level of the network, and patients who need timely, accurate
information on their own phones without a hospital visit.

Two problems had to be settled together:

1. **Facility → state transport under hostile conditions.** A direct
   facility→state uplink loses data whenever a facility's connectivity,
   the state's ingress, or anything between them blinks. Retrying every
   event with equal priority also means a telemetry flood starves a
   referral.
2. **The last mile to the patient.** Lab results, pickup notices,
   referrals and immunization reminders must reach patients on any
   phone — smartphone, feature phone, or shared handset — without
   leaking clinical detail to whoever holds the device.

## Decision

**A three-tier topology — facility edge → LGA hub → state plane — with a
store-and-forward priority hub as a first-class service, and a
notification rail with patient-owned channels as a first-class service.**

### 1. LGA hub (`services/lga-hub`)

One hub per Local Government Area (~774 nationally), sitting between the
facility edges and the state plane:

- **Same envelope, no translation.** Events are the sync-ingest envelope
  (`{id, facility, type, ts, payload}`) end to end. The hub never
  mutates payloads — it classifies, persists, orders, and forwards.
- **Priority classes.** `Classify()` maps event types to `critical`
  (referrals, emergencies, allergy alerts, anything flagged
  `danger: true`), `standard` (encounters, prescriptions, claims, lab
  results, vitals, appointments), or `bulk` (telemetry). Forwarding
  drains critical → standard → bulk on every pass, so a referral always
  overtakes a metrics batch.
- **Durability before acknowledgment.** With `SPILL_PATH` set, every
  event is appended to a JSONL spill file and `fsync`ed before the hub
  answers. Forwarded marks are also persisted (durable `{"ack": id}`
  lines, fsynced before returning), so a power cut between forward and
  mark never resurrects an already-sent event. Replay at boot applies
  data lines and ack lines; compaction rewrites the file past 10 000
  retired entries.
- **Idempotent ingest.** Duplicate event IDs are acknowledged but not
  re-stored (`{received, duplicates}`), so facility sync agents can
  retry freely.
- **Graceful upstream failure.** When the state plane is unreachable the
  hub stops the pass, stamps `last_upstream_error`, and keeps buffering —
  observable at `GET /stats`, nothing is dropped.
- **Memory mode for dev.** Without `SPILL_PATH` the same store interface
  runs in-memory; the forwarding and classification logic is identical.

### 2. Notification rail (`services/notification-service`)

Domain events become patient-facing messages through a seam, never a
proprietary SDK:

- **Subscriptions are explicit.** A patient row is
  `{patient_id, phone, language, facility}`; phone numbers are
  normalized (`0803…` → `+234803…`). The rail never guesses a number —
  no subscription, no message, and the event is recorded as skipped.
- **Provider seam.** `SMSProvider` has two implementations: `LogProvider`
  (dev/default, logs the message) and `WebhookProvider` (generic JSON
  POST with optional bearer token, 8 s timeout — works with any
  aggregator, no vendor lock-in, licence-clean per ADR-0005).
- **Delivery states with persisted attempts.** `queued → sent | failed`.
  Every send attempt increments a **persisted** counter
  (`UPDATE … SET attempts = attempts + 1 … RETURNING attempts`), so a
  crash mid-drain can neither lose the attempt count nor double-send a
  message that already reached `sent`. Three attempts, then `failed`
  with the last error retained.
- **Content discipline.** Messages are prefixed `OpenHealth:`, capped
  under 320 characters (two SMS segments), and never carry clinical
  detail — "your lab result is ready at the facility", never the result.
  Rendering is per-language, five languages (en/pcm/ha/yo/ig).

### 3. Patient channels

- **Patient PWA (`apps/patient`).** A separate, calmer shell from the
  clinician PWA: sign-in with patient code + phone, an offline feed
  (IndexedDB-free — localStorage only, because the feed is small),
  action-needed grouping (referrals, pickups, lab-ready notices within a
  14-day window surface first), relative timestamps, five languages,
  48 px touch targets. The service worker precaches the shell and never
  caches `/api/`.
- **Portal feed endpoint.** `GET /portal/feed?patient_id=&phone=`
  returns the patient's notifications only when the phone matches the
  subscription on file; **any mismatch is a 404**, so the endpoint
  cannot be used to enumerate patients.
- **USSD remains a peer channel** (`services/ussd-gateway`) for feature
  phones; the notification rail is channel-agnostic about how the feed
  is read.

### Topology

```
facility edge (PWA + sync-agent, offline-first)
      │  POST /ingest (retry-safe, idempotent)
      ▼
LGA hub (classify → spill+fsync → priority forward)
      │  POST /ingest/raw (NDJSON batches, critical→standard→bulk)
      ▼
state plane ×37 (sync-ingest → Postgres/Kafka/Medplum, ADR-0006/0008)
      │
      ▼
notification-service (domain events → SMS via provider seam)
      ▼
patient (SMS on any phone · patient PWA · USSD)
```

## Consequences

- A facility never needs the state plane to be reachable to keep working;
  the LGA hub is the shock absorber, and it is itself power-cut-proof.
- A telemetry burst can never delay a referral past one forwarding pass.
- Patients are reachable on any handset without the platform ever
  sending clinical detail over SMS or leaking who is a patient.
- Adding a new aggregator is config (`SMS_WEBHOOK_URL`), not code.
- The hub is deliberately dumb: no transformation, no clinical logic.
  Anything smarter would create a fourth schema to keep consistent
  across 774 sites.

## Verification

- `services/lga-hub` — 6 unit/integration tests: classification table,
  dedupe, priority forward order against a live test upstream, dead-
  upstream buffering, spill restart without resurrection, idempotent
  ingest endpoint.
- `services/notification-service` — 7 unit/HTTP tests plus a
  `TEST_DATABASE_URL`-gated Postgres store suite (upsert, persisted
  attempts, double-send guard, stats).
- `demo/national-sync.sh` — end-to-end: mixed batch forwarded in
  priority order, duplicate retry acknowledged, hub killed mid-flight
  (`pkill`) and restarted from the spill with queues intact, new events
  flowing after restart, exactly 4 unique IDs arriving upstream.
- CI: both modules in the Go vet/test loop; national-sync smoke asserted
  in the e2e job; patient PWA tests and self-containment grep in the
  clients job.
