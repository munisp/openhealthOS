#!/usr/bin/env python3
"""Phase 1 bridge: Asterisk AudioSocket <-> Pipecat <-> OpenHealth converse.

This is the thin shell around packages/voicebridge (the tested policy core).
The shell owns audio: PCM in -> faster-whisper STT -> utterance -> core ->
reply text -> Piper TTS -> PCM out. Pipecat (BSD-2-Clause) provides the
pipeline plumbing (VAD, barge-in, turn-taking); when unavailable (e.g. in CI
without audio hardware) the core alone still passes its full test suite.

Deploy-time dependencies (not vendored here — compose builds them):
    pipecat-ai, faster-whisper, piper-tts (all MIT/BSD class)

Run inside the bridge container; DISPATCH_URL points at dispatch-service.
"""

import os
import struct
import sys

# The tested policy core ships in the platform repo; the compose build mounts
# packages/voicebridge into the image (see Dockerfile).
sys.path.insert(0, os.environ.get("VOICEBRIDGE_PATH", "/opt/voicebridge"))
from voicebridge import BridgeSession, ConverseClient  # noqa: E402

DISPATCH_URL = os.environ.get("DISPATCH_URL", "http://127.0.0.1:8093")
AUDIOSOCKET_PORT = int(os.environ.get("AUDIOSOCKET_PORT", "9092"))


def serve():
    """AudioSocket server: one connection per call, uuid header first,
    then 8kHz s16le PCM frames. VAD/turn-taking segment utterances."""
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", AUDIOSOCKET_PORT))
    srv.listen(64)
    print(f"bridge listening on :{AUDIOSOCKET_PORT}, dispatch at {DISPATCH_URL}")
    while True:
        conn, _ = srv.accept()
        handle_call(conn)


def handle_call(conn):
    """One call. Frame format: [type:1][len:2 BE][payload]. type 0x01 =
    uuid, 0x00 = hangup, 0x10 = PCM. DTMF arrives as RFC 2833 on the RTP
    side; Asterisk surfaces it via the dialplan Read() fast path instead."""
    call_id = read_uuid(conn)
    caller = "unknown"  # production: read X-CallerID SIP header via ARI
    session = BridgeSession(ConverseClient(DISPATCH_URL), caller, call_id)
    speech = bytearray()
    for frame in frames(conn):
        kind, payload = frame
        if kind == 0x00:  # hangup
            break
        if kind != 0x10:
            continue
        speech.extend(payload)
        if utterance_complete(speech):       # Pipecat VAD in production
            text = transcribe(bytes(speech))  # faster-whisper server
            speech.clear()
            directive = session.on_utterance(text)
            if directive["say"]:
                speak(conn, directive["say"])  # Piper TTS -> PCM frames
            if directive["hangup"]:
                break
    conn.close()


def frames(conn):
    """Yield (kind, payload) AudioSocket frames."""
    while True:
        hdr = conn.recv(3)
        if len(hdr) < 3:
            return
        kind = hdr[0]
        ln = struct.unpack(">H", hdr[1:3])[0]
        data = b""
        while len(data) < ln:
            chunk = conn.recv(ln - len(data))
            if not chunk:
                return
            data += chunk
        yield kind, data


def read_uuid(conn):
    for kind, payload in frames(conn):
        if kind == 0x01:
            return payload.hex()
    return "unknown"


# ---- audio edges (implemented by Pipecat services in the container) ----

def utterance_complete(pcm):
    raise NotImplementedError("provided by Pipecat VAD in the container image")


def transcribe(pcm):
    raise NotImplementedError("provided by faster-whisper server in the image")


def speak(conn, text):
    raise NotImplementedError("provided by Piper TTS in the image")


if __name__ == "__main__":
    serve()
