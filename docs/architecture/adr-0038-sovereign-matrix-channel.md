# ADR-0038: Sovereign Matrix channel (self-hosted WhatsApp alternative)

- Status: accepted
- Date: 2026-10-20
- Supersedes: nothing. Complements ADR-0037 (WhatsApp channel).

## Context

ADR-0037 put the patient channel on WhatsApp because that is where Nigerian
patients already are. But WhatsApp is rented ground: Meta owns the transport,
the client, the terms of service, and the kill switch. A national health
platform that can be de-platformed by a terms-of-service change is not
sovereign. We evaluated zooid (MIT, Matrix-based team chat) and the answer
was: keep WhatsApp for reach, add a self-hosted Matrix transport for control.

## Decision

The whatsapp-gateway dialogue engine now speaks **two transports through one
Sender seam**:

1. **WhatsApp** (ADR-0037) — maximum patient reach, Meta-dependent.
2. **Matrix** (this ADR) — a homeserver the platform operator self-hosts
   (Synapse/Dendrite, both Apache-2.0). The bot is an ordinary Matrix user:
   - **Pull-mode sync**: long-polls `GET /_matrix/client/v3/sync`, no webhook
     endpoint to expose, no inbound firewall rule, works behind NAT — the
     zooid pattern, re-implemented clean-room in ~120 lines, no SDK.
   - **Send**: `PUT /_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}`.
   - Enabled only when `MATRIX_HOMESERVER` + `MATRIX_ACCESS_TOKEN` are set;
     otherwise the service is WhatsApp-only, unchanged.

## Identity without MSISDN

WhatsApp gives a verified phone number for free; Matrix does not. So the
binding flow is explicit and fail-closed:

1. Patient says anything → bot asks for **patient ID + registered phone**.
2. The pair is checked against the portal rail (`/portal/coverage` probe) —
   the same portalGate honesty rule as WhatsApp: a pair that does not match
   any record gets a 404-indistinguishable rejection (`mx_bind_fail`), never
   a hint about which half was wrong.
3. Sessions are keyed `mx:{userID}`; the Matrix user ID is never trusted as
   identity, only as a session handle.

## Consequences

- **Sovereign option**: a state or NGO can run the entire channel on its own
  hardware, offline-capable, end-to-end encrypted by the homeserver, no Meta.
- **One engine, two channels**: booking, coverage, results, wellness, and the
  5-language i18n (en/pcm/ha/yo/ig) work identically on both — the dialogue
  engine never knows which transport delivered the message.
- **Outbound, sovereign-first**: the notification rail (ADR-0013/0015) gains a
  `matrix:` provider leg. At send time it resolves phone → bound Matrix room
  through the gateway's `/internal/matrix-room` (bound sessions only, bare
  404 otherwise) and delivers via the homeserver. Unbound patients or a dead
  homeserver fail the leg, the circuit breaker opens, and SMS legs take over
  — sovereignty is preferred, never required. Content-derived txn ids make
  chain retries idempotent. Verified by `demo/matrix-notify.sh`: FHIR
  Observation → drain → patient's Matrix room; unbound phone falls through.
- **No new licence surface**: Matrix protocol is open; Synapse/Dendrite are
  Apache-2.0; our listener is original code (compose, don't fork — ADR-0005).
- Verified end-to-end by `demo/matrix.sh`: bind → book (6 steps) → coverage →
  language switch → wrong-pair fail-closed, against a fake homeserver.
