# ADR-0025: Reference voice stacks — Phase 1 (Asterisk + Pipecat), Phase 2 (LiveKit + Kamailio)

Status: accepted
Date: 2026-10-19

## Context

ADR-0022/0024 built the platform side of voice (`/webhooks/voice`,
`/webhooks/voice/converse`, deterministic triage). What was missing is the
deployable media stack between the GSM network and those endpoints. The
stack must: run fully on-prem (offline-first doctrine), cover Nigerian
languages, carry no forbidden licence, and scale from a state pilot to the
national emergency number without re-engineering behaviour.

## Decision

**One policy core, two swappable media stacks.**

`packages/voicebridge/` (stdlib-only, fully tested) owns ALL emergency-line
policy: turn sequencing, DTMF-1 fast path, silence handling (never hang up
an open call), done-call reassurance, retry prompts. Both stacks are thin
audio shells around it, so migrating from Phase 1 to Phase 2 cannot change
caller-visible behaviour — the same unit tests pin both.

**Phase 1 — `deploy/voice/phase1-asterisk-pipecat/`** (state pilot, ~500
concurrent calls): Asterisk 20 (GPLv2 — protocol use explicitly does not
contaminate our code) terminates trunks; dialplan `Read()` gives the DTMF-1
fast path; AudioSocket streams PCM to a Pipecat (BSD-2-Clause) bridge;
faster-whisper (MIT) STT and Piper (MIT) TTS run in containers beside it.

**Phase 2 — `deploy/voice/phase2-livekit/`** (national, >1,000 concurrent,
plus telemedicine video on the same fabric): Kamailio (GPLv2) SIP perimeter
with pike flood-filtering and trunk failover; RTPengine (GPLv3, media plane
only); LiveKit server + SIP + Agents (all Apache-2.0) as the media fabric;
the agent joins `emergency-*` rooms and drives the identical voicebridge
core.

**Model choices:** faster-whisper `small` (MIT; covers Yoruba, Hausa, Igbo).
Meta MMS was evaluated and rejected — CC-BY-NC is non-commercial and a
public health service cannot use it. Ollama (MIT) is wired but disabled by
default: the LLM may rephrase at the edge, triage never leaves deterministic
code. Vocode (abandoned) and TEN Framework (non-compete rider — not actually
open source) were evaluated and rejected.

## Consequences

- A state can stand up the emergency line with `docker compose up` against
  any GSM gateway or NCC-licensed interconnect; nothing phones home.
- Copyleft components are composed over protocols (AudioSocket/SIP/RTP),
  never linked — doctrine-compliant.
- `demo/voice.sh` proves the contract end-to-end against a live
  dispatch-service: DTMF fast path, a three-turn conversational obstetric
  SOS triaged red, done-call reassurance, and the never-hang-up silence rule.
- Phase 2's LiveKit fabric is the future home of video consults; that work
  is the PWA video UI + room type, not new media infrastructure.
