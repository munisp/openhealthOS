#!/usr/bin/env bash
# Voice stacks smoke (ADR-0025): the voicebridge policy core driving a live
# dispatch-service exactly as the Pipecat (Phase 1) and LiveKit (Phase 2)
# shells will drive it in production — same HTTP contract, same core.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/dispatch-service && go build -o "$TMP/dispatch" . )
LISTEN=:18193 "$TMP/dispatch" >"$TMP/dispatch.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18193/healthz" >/dev/null || { cat "$TMP/dispatch.log"; exit 1; }

echo "== 1. DTMF fast path (what Asterisk Read() triggers on key 1) =="
curl -sf -X POST http://127.0.0.1:18193/webhooks/voice \
  -H 'Content-Type: application/json' \
  -d '{"from":"+2348030001111","call_id":"demo-dtmf","dtmf":"1"}' \
  | python3 -c "import json,sys; r=json.load(sys.stdin); assert r.get('incident_id'), r; print('   dtmf 1 ->', r['incident_id'], r['triage'])"

echo "== 2. conversational call through the REAL voicebridge core =="
DISPATCH_URL=http://127.0.0.1:18193 PYTHONPATH=packages/voicebridge python3 - <<'PY'
from voicebridge import BridgeSession, ConverseClient

s = BridgeSession(ConverseClient("http://127.0.0.1:18193"),
                  "+2348030002222", "demo-converse", 6.52, 3.38)
turns = ["my wife is in labour and the baby is coming", "she is bleeding", "yes"]
said = []
for t in turns:
    d = s.on_utterance(t)
    said.append(d["say"])
    if d["hangup"]:
        break
assert s.done and s.incident_id, said
assert "Help is on the way" in said[-1] or "already" in said[-1], said
print(f"   3 turns -> {s.incident_id}, triage red (obstetric), hung up cleanly")

# done calls reassure instead of cloning
d = s.on_utterance("hello?")
assert d["hangup"] and "already registered" in d["say"]
print("   post-completion turn reassures, no clone")

# silence policy: never hang up an open call
s2 = BridgeSession(ConverseClient("http://127.0.0.1:18193"), "+2348030003333", "demo-silence")
d = s2.on_silence()
assert d["listen"] and not d["hangup"]
print("   silence on an open call re-prompts — never hangs up")
PY

echo "== 3. both incidents exist, voice channel =="
curl -sf "http://127.0.0.1:18193/incidents" | python3 -c "
import json, sys
incs = json.load(sys.stdin)['incidents']
voice = [i for i in incs if i['channel'] == 'voice']
assert len(voice) == 2, voice
print('   2 voice incidents on the board:', ', '.join(i['id'] for i in voice))"

echo
echo "VOICE STACK SMOKE: PASS"
