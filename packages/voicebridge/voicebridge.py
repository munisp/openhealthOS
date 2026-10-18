"""Voice bridge core — the deterministic half of every voice stack.

Both reference stacks (Phase 1: Asterisk + Pipecat; Phase 2: LiveKit Agents)
are thin shells around this module. The shells own audio (STT in, TTS out);
this core owns the *policy*: what to ask the dispatch-service, when to keep
listening, when to hang up, and the DTMF-1 fast path. Keeping policy here —
stdlib-only, fully unit-tested — means swapping media stacks never changes
emergency-line behaviour.

Contract with dispatch-service (ADR-0022/0024):
  POST /webhooks/voice           {from, dtmf, call_id, lat, lng}
  POST /webhooks/voice/converse  {from, call_id, utterance, lat, lng}
Both always answer 200 with {"received", "say"|"play", ...}.
"""

import json
import urllib.request


class Error(Exception):
    pass


class ConverseClient:
    """HTTP transport. Inject `poster(url, payload) -> dict` for tests or
    for a stack-local transport (e.g. in-process call in unit rigs)."""

    def __init__(self, base_url, poster=None, timeout=5.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.poster = poster or self._urllib_post

    def _urllib_post(self, path, payload):
        req = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def dtmf(self, from_number, call_id, digit, lat=0.0, lng=0.0):
        return self.poster("/webhooks/voice", {
            "from": from_number, "call_id": call_id,
            "dtmf": digit, "lat": lat, "lng": lng})

    def converse(self, from_number, call_id, utterance, lat=0.0, lng=0.0):
        return self.poster("/webhooks/voice/converse", {
            "from": from_number, "call_id": call_id,
            "utterance": utterance, "lat": lat, "lng": lng})


class BridgeSession:
    """One call. The shell feeds it events; it returns speech directives.

    Directives: {"say": str, "listen": bool, "hangup": bool}
    """

    MAX_EMPTY_TURNS = 2  # then we give up gracefully and keep the DTMF path

    def __init__(self, client, from_number, call_id, lat=0.0, lng=0.0):
        if not from_number or not call_id:
            raise Error("a call must name its number and call id")
        self.client = client
        self.from_number = from_number
        self.call_id = call_id
        self.lat = lat
        self.lng = lng
        self.done = False
        self.empty_turns = 0
        self.incident_id = None

    def on_dtmf(self, digit):
        """Keypad fast path: 1 files the SOS immediately, no conversation."""
        resp = self.client.dtmf(self.from_number, self.call_id, digit,
                                self.lat, self.lng)
        if not resp.get("received"):
            return {"say": resp.get("play", "Please try again."),
                    "listen": False, "hangup": False}
        if resp.get("incident_id"):
            self.incident_id = resp["incident_id"]
            self.done = True
            return {"say": resp["play"], "listen": False, "hangup": True}
        # greeting / re-menu
        return {"say": resp.get("play", ""), "listen": False, "hangup": False}

    def on_utterance(self, text):
        if self.done:
            return {"say": "Your emergency is already registered. Help is "
                           "being arranged. Keep this phone on.",
                    "listen": False, "hangup": True}
        text = (text or "").strip()
        if not text:
            self.empty_turns += 1
            if self.empty_turns >= self.MAX_EMPTY_TURNS:
                return {"say": "I cannot hear you. Please stay on the line "
                               "or press 1 to register an emergency.",
                        "listen": True, "hangup": False}
            return {"say": "Sorry, I did not catch that. What is the emergency?",
                    "listen": True, "hangup": False}
        self.empty_turns = 0
        resp = self.client.converse(self.from_number, self.call_id, text,
                                    self.lat, self.lng)
        if not resp.get("received"):
            return {"say": resp.get("say", "Please say that again."),
                    "listen": True, "hangup": False}
        if resp.get("done"):
            self.done = True
            self.incident_id = resp.get("incident_id", self.incident_id)
            return {"say": resp["say"], "listen": False, "hangup": True}
        return {"say": resp.get("say", ""), "listen": True, "hangup": False}

    def on_silence(self):
        """Caller went quiet. Never hang up an emergency line on silence —
        re-prompt, because the caller may be tending the patient."""
        if self.done:
            return {"say": "", "listen": False, "hangup": True}
        return {"say": "Are you still there? Tell me what is happening, "
                       "or press 1.", "listen": True, "hangup": False}
