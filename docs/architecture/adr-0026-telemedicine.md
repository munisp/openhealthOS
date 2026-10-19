# ADR-0026: Telemedicine — async-first, voice/video rooms on the Phase 2 fabric

Status: accepted
Date: 2026-10-19

## Context

"Do we need WebRTC for telemedicine?" resolved to: yes for the live-video
tier — and LiveKit (ADR-0025 Phase 2) is already a WebRTC SFU, so video adds
no new media infrastructure. But live video is the wrong first tier for
Nigerian bandwidth: a stable video consult wants ~600 kbps bidirectional;
most rural demand is met asynchronously, and a failed video call is worse
than no video call. The platform's advantage over a WhatsApp call is not
pixels — it is that the consult is consented, scheduled, documented,
billable, and quality-measurable inside the record.

## Decision

**Three tiers, shipped in dependency order.** `services/telemed-service`
(Go, stdlib, :8094):

1. **Async (2G-safe, the default).** A consult is a threaded record:
   structured complaint + photo references (blobs live in object storage;
   threads store pointers, never bytes). State machine
   `submitted → triaged → in_review → advice_given → completed`, with
   `escalated_to_call` and `cancelled`. A clinician's first message flips
   the thread to `advice_given`. NDPA consent is mandatory at intake and
   rejected consults consume no ID. Advice SLA (routine 4h, urgent 1h) feeds
   an `/overdue` list — unanswered consults are a visible breach, not a
   silent failure.
2. **Voice room (3G).** Escalation provisions a LiveKit room atomically with
   the state move — a call never exists without its room. Participants mint
   HS256 join tokens (`rooms.go`, stdlib HMAC-SHA256, no SDK) with LiveKit's
   documented grant claims; `VerifyLiveKitToken` lets tests and clients
   sanity-check.
3. **Video room (decent 3G+/4G).** Same room, same token; the tier is
   recorded in token metadata and the PWA enables the camera only for the
   video tier. Adaptive bitrate/simulcast are LiveKit client concerns.

**PWA:** `apps/pwa/telemed.html` — async thread + escalation buttons + join
flow that lazy-loads the LiveKit client SDK only when a call actually
happens, keeping the 2G-first shell light. Precached in `sw.js` (cache bump
`openhealth-shell-v5`).

## Consequences

- Tele-consults are first-class records: consented (NDPA), auditable threads,
  SLA-measured, and closable — everything a personal-WhatsApp consult is not.
- Token minting works fully offline at the edge (HMAC, no network), so a
  state running its own LiveKit fabric never depends on a cloud control plane.
- Honest limits: room provisioning assumes the Phase 2 fabric is deployed;
  without it, tiers 2–3 return tokens that cannot connect (the async tier is
  unaffected). Media blob storage is referenced, not implemented — S3-compatible
  object storage (e.g. MinIO, AGPL — compose, don't embed) per deployment.
- `demo/telemed.sh` proves: consent gate, async thread with photo reference,
  advice state flip, atomic room escalation, dual token minting with
  verified signatures, closed-thread enforcement.
