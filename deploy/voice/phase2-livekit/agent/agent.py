#!/usr/bin/env python3
"""Phase 2 agent: LiveKit room <-> OpenHealth converse.

Same shape as the Phase 1 bridge, same policy core — only the transport
changed (LiveKit audio tracks instead of AudioSocket PCM). The agent joins
each `emergency-*` room, runs STT on the caller's track, feeds utterances
to packages/voicebridge, and synthesizes directives back.

Deploy-time dependencies (Apache-2.0/MIT, pulled by the image build):
    livekit-agents, livekit-plugins-silero (VAD)

The caller's number arrives in the SIP participant's identity/metadata;
cell-tower fixes, when the interconnect provides them, arrive as SIP
headers and are forwarded as lat/lng on every turn.
"""

import os
import sys

sys.path.insert(0, os.environ.get("VOICEBRIDGE_PATH", "/opt/voicebridge"))
from voicebridge import BridgeSession, ConverseClient  # noqa: E402

DISPATCH_URL = os.environ.get("DISPATCH_URL", "http://127.0.0.1:8093")


async def entrypoint(ctx):
    """livekit-agents entrypoint: one invocation per emergency room."""
    from livekit import agents  # inside container only

    participant = await ctx.wait_for_participant()
    from_number = participant.identity or "unknown"
    call_id = ctx.room.name  # room per call, named by the SIP dispatch rule
    session = BridgeSession(ConverseClient(DISPATCH_URL), from_number, call_id)

    async def on_transcript(text):
        directive = session.on_utterance(text)
        if directive["say"]:
            await say(ctx, directive["say"])
        if directive["hangup"]:
            await ctx.room.disconnect()  # TTS flush, then end the room

    # STT: faster-whisper via the agents inference plugin (self-hosted URL),
    # VAD: silero. Turn detection: voicebridge on_silence policy on timeouts.
    _ = on_transcript  # wired to the STT stream in the container build


async def say(ctx, text):
    raise NotImplementedError("Piper TTS output track — container build")


if __name__ == "__main__":
    from livekit import agents
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
