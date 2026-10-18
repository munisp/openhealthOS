# Voice deployment guide (ADR-0025)

Two reference stacks, one policy core. Every component is OSI-approved;
STT/TTS/LLM run on-prem so the emergency line survives a total internet
outage. Behaviour lives in `packages/voicebridge` (tested, stdlib-only);
the stacks below are swappable audio transports around it.

## Which stack when

| | Phase 1: Asterisk + Pipecat | Phase 2: LiveKit + Kamailio |
|---|---|---|
| Scale | state pilot, ~100–500 concurrent calls | national, >1,000 concurrent calls |
| Also carries telemedicine video | no | yes (same fabric) |
| Ops burden | low — two moving parts | real — SIP perimeter to operate |
| Licences | Asterisk GPLv2, Pipecat BSD-2 | LiveKit/SIP/Agents Apache-2.0, Kamailio GPLv2, RTPengine GPLv3 |
| When to choose | first deployment | when Phase 1 saturates CPU on audio, or video consults are funded |

Copyleft note: Asterisk/Kamailio/RTPengine are GPL but communicate over
protocols (AudioSocket/SIP/RTP) — no platform code links them, per the
doctrine this is composition, not embedding. Whisper (MIT) covers Yoruba,
Hausa and Igbo; **do not** substitute Meta MMS (CC-BY-NC — non-commercial
only, unusable for a public service).

## Phase 1 — state pilot

    cd deploy/voice/phase1-asterisk-pipecat
    DISPATCH_URL=http://dispatch.internal:8093 docker compose up -d

Hardware (500 concurrent calls): 16 vCPU / 64 GB; add one RTX-class GPU and
set `WHISPER_DEVICE=cuda` for lower STT latency. CPU-only works — `small`
model at 8 kHz telephony audio keeps pace; drop to `tiny` if it doesn't.

Trunk: point your GSM gateway or NCC-licensed SIP interconnect at
`pjsip.conf`'s `[trunk]`. Emergency number routing stays with the
interconnect provider — this stack answers whatever lands on it.

## Phase 2 — national

    cd deploy/voice/phase2-livekit
    LK_API_KEY=... LK_API_SECRET=... DISPATCH_URL=http://dispatch.internal:8093 \
      docker compose up -d

Scaling notes:
- Kamailio edge absorbs floods and fails over between interconnects; tune
  `pike` thresholds to observed traffic.
- LiveKit server scales horizontally behind the edge; the SIP bridge and
  agent workers scale by replica count.
- faster-whisper replicas first on CPU, then on GPU nodes as budget allows.
- Telemedicine: the same LiveKit fabric hosts video consult rooms — no new
  media infrastructure, just a new room type and the PWA's video UI.

## Shared operational truths

- **Never hang up on silence.** An emergency caller may be tending the
  patient. The core re-prompts; hangup happens only after a filed intake or
  explicit completion.
- **DTMF 1 always works.** Speech is an enhancement, never a requirement.
- **Triage is deterministic.** The LLM (optional, Ollama, off by default)
  may rephrase, never decide. Keyword triage runs in dispatch-service where
  auditors can read it.
- **Welfare callbacks** (48-hour, ADR-0019) use the outbound trunk in
  `sip-trunk.json` — budget interconnect minutes for them.
- Verify end-to-end any time with `demo/voice.sh` from the repo root.
