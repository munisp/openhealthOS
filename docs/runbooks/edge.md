# Runbook: facility edge node (L1)

The edge node is the facility's server: a ₦1.8M ($1,200) mini-PC on solar
with 48h battery autonomy. **Everything clinical works with the internet
down.** Connectivity only drains queues.

## Hardware profile

- Mini-PC, 4-core, 16 GB RAM, 1 TB NVMe; ~15 W average draw
- Solar + 300 Wh LFP battery (48 h autonomy); charge controller streams
  telemetry to the infrastructure module
- Facility Wi-Fi/LAN; staff Android phones (5+ years old) install the PWA
  from `http://<edge>/` — no Play Store, no external network needed

## Stack (`infra/compose/edge.yaml`)

| Service | Role |
|---|---|
| `pwa` | serves the offline-first app to facility devices |
| `sync-agent` | store-and-forward: event outbox + CRDT replica → state plane |
| `telemetry-collector` | solar/battery/water/cold-chain readings into the outbox |

## Offline guarantees

1. **Writes never wait on the network.** Local stores are journal-backed
   (fsync before ack — same pattern as the ledger, ADR-0007); a power cut
   mid-write loses nothing already acknowledged.
2. **At-least-once upload.** The outbox cursor advances only on a 2xx ack;
   the CRDT replica re-uploads only when content changed. Redelivery is
   safe: ingest dedups events by id and doc merge is idempotent.
3. **30+ days fully offline.** Outbox and replica simply grow; the NFR
   budget is <1 MB/facility-day on the wire once connectivity returns.
4. **Self-heal.** `restart: unless-stopped` on every service; on boot the
   sync agent resumes from the ack cursors — no operator action.

## Conflict handling

Clinical facts (immunisations, allergies, danger signs) are add-only sets —
they union on merge and can never be lost or erased, only superseded by a
new recorded fact. Scalar fields are last-writer-wins by hybrid logical
clock. Genuine clinical ambiguity (two different values for the same
scalar within the same minute) surfaces in the PWA's conflict panel for a
clinician to resolve — the platform never silently picks for clinical
judgment.

## SMS/USSD fallback

Eligibility checks, enrolment confirmations, and cold-chain/power alerts
flow through the USSD gateway (`services/ussd-gateway`) when data
connectivity is absent — the telco webhook path is deliberately auth-exempt
and signature-verified instead.

## Signed OTA updates

Edge fleets only run signed, staged images — see `tools/ota` (ed25519
manifest signing, deterministic cohort rollout, one-command rollback).
Never `docker pull` latest on an edge node; promote through stages:
pilot cluster → LGA → state.
