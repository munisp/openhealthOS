#!/bin/sh
# demo/matrix-notify.sh — sovereign-first notification routing (ADR-0038):
# a lab result enters the notification rail as a FHIR Observation; the
# matrix leg resolves the patient's phone to a bound howUdeyChat room via
# the gateway's binding table and delivers it on our own homeserver.
# An unbound patient falls through the chain to SMS. No Meta, no aggregator.
set -eu
cd "$(dirname "$0")/.."
: > /tmp/mx-events.jsonl

cat > /tmp/mxn-stub.py <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[2]          # "hs" (homeserver) or "pl" (platform)
SENT = []                   # outbound messages: {room, body}

class H(BaseHTTPRequestHandler):
    def _j(self, obj, code=200):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        p = self.path.split("?")[0]
        if MODE == "hs":
            if p == "/control/sent":
                return self._j({"sent": SENT})
            if p == "/_matrix/client/v3/account/whoami":
                return self._j({"user_id": "@oh-bot:hs"})
            if p == "/_matrix/client/v3/sync":
                evs = []
                if os.path.exists("/tmp/mx-events.jsonl"):
                    os.replace("/tmp/mx-events.jsonl", "/tmp/mx-events.reading")
                    with open("/tmp/mx-events.reading") as f:
                        for line in f:
                            d = json.loads(line)
                            evs.append({"event_id": d["id"], "sender": d.get("sender", "@amina:hs"),
                                        "type": "m.room.message",
                                        "content": {"msgtype": "m.text", "body": d["body"]}})
                rooms = {"!room1:hs": {"timeline": {"events": evs}}} if evs else {}
                return self._j({"next_batch": "n1", "rooms": {"join": rooms}})
            return self._j({}, 404)
        if p == "/portal/coverage":
            if "OH-KD-0001" in self.path and "2348011112222" in self.path:
                return self._j({"status": "eligible", "scheme": "NHIA Basic"})
            return self._j({"error": "not found"}, 404)
        return self._j({}, 404)

    def do_PUT(self):
        if "/send/m.room.message/" in self.path:
            room = self.path.split("/rooms/")[1].split("/")[0]
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["body"]
            SENT.append({"room": room, "body": body})
            return self._j({"event_id": "$ok"})
        return self._j({}, 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._j({"status": "ok"})

    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

python3 /tmp/mxn-stub.py 18311 hs & HS_PID=$!
python3 /tmp/mxn-stub.py 18310 pl & PL_PID=$!
(cd services/whatsapp-gateway && go build -o /tmp/wa-gateway .)
(cd services/notification-service && go build -o /tmp/ntf .)

LISTEN=":18312" REGISTRY_URL=http://127.0.0.1:18310 \
  PORTAL_URL=http://127.0.0.1:18310 WELLNESS_URL=http://127.0.0.1:18310 \
  MATRIX_HOMESERVER=http://127.0.0.1:18311 MATRIX_ACCESS_TOKEN=tok \
  /tmp/wa-gateway >/tmp/mxn-gw.log 2>&1 & WA_PID=$!

LISTEN=":18313" SMS_PROVIDERS="matrix:http://127.0.0.1:18312,log" \
  MATRIX_HOMESERVER=http://127.0.0.1:18311 MATRIX_ACCESS_TOKEN=tok \
  /tmp/ntf >/tmp/mxn-ntf.log 2>&1 & NTF_PID=$!
trap 'kill $HS_PID $PL_PID $WA_PID $NTF_PID 2>/dev/null || true' EXIT
sleep 2

sent_count() { curl -s http://127.0.0.1:18311/control/sent | python3 -c "import json,sys;print(len(json.load(sys.stdin)['sent']))"; }
say() { # inject inbound matrix message, wait for the reply
  n=$(sent_count)
  python3 -c "import json,sys;open('/tmp/mx-events.jsonl','a').write(json.dumps({'id':sys.argv[1],'body':sys.argv[2]})+'\n')" "$1" "$2"
  i=0; while [ $i -lt 50 ]; do [ "$(sent_count)" -gt "$n" ] && return 0; sleep 0.5; i=$((i+1)); done
}
check() { curl -s http://127.0.0.1:18311/control/sent | grep -q "$1" \
  && echo "ok: $2" || { echo "FAIL: $2 — $(curl -s http://127.0.0.1:18311/control/sent)"; exit 1; }; }

echo "== patient binds on the matrix channel"
say m1 hi
say m2 "OH-KD-0001 08011112222"
check "Book an appointment" "bound -> menu"

echo "== subscription exists on the notification rail"
curl -sf -X POST http://127.0.0.1:18313/subscriptions -H 'Content-Type: application/json' \
  -d '{"patient_id":"OH-KD-0001","phone":"08011112222","facility_id":"FAC-1"}' >/dev/null && echo "ok: subscribed"

echo "== lab posts a FHIR Observation (result ready)"
curl -sf -X POST http://127.0.0.1:18313/fhir/inbox -H 'Content-Type: application/json' -d '{
  "resourceType": "Observation", "id": "lab-1",
  "category": [{"coding": [{"code": "laboratory"}]}],
  "code": {"text": "Malaria RDT"},
  "subject": {"reference": "Patient/OH-KD-0001"}}' >/dev/null && echo "ok: fhir inbox accepted"

echo "== drain delivers sovereign-first into the matrix room"
curl -sf -X POST http://127.0.0.1:18313/drain >/dev/null
sleep 1
check "test result" "lab notification delivered to the matrix room"
check "Malaria RDT" "test name carried, no clinical detail beyond it"

echo "== unbound patient falls through the chain (no matrix binding)"
curl -sf -X POST http://127.0.0.1:18313/subscriptions -H 'Content-Type: application/json' \
  -d '{"patient_id":"OH-KD-0002","phone":"08099998888","facility_id":"FAC-1"}' >/dev/null
curl -sf -X POST http://127.0.0.1:18313/events -H 'Content-Type: application/json' -d '{
  "id":"ev2","facility":"FAC-1","type":"prescription","ts":"2026-10-20T00:00:00Z",
  "payload":{"patient_id":"OH-KD-0002","drug_name":"Coartem"}}' >/dev/null
n=$(sent_count)
curl -sf -X POST http://127.0.0.1:18313/drain >/dev/null
sleep 1
[ "$(sent_count)" = "$n" ] && echo "ok: no matrix delivery for unbound phone (fell through to log leg)" \
  || { echo "FAIL: unbound phone reached the homeserver"; exit 1; }
curl -s "http://127.0.0.1:18313/notifications?patient=OH-KD-0002" | grep -q '"state":"sent"' \
  && echo "ok: fallback leg still delivered (state sent)" || { echo "FAIL: fallback delivery"; exit 1; }

echo "== unit batteries"
(cd services/whatsapp-gateway && go test ./... >/dev/null) && echo "ok: whatsapp-gateway tests"
(cd services/notification-service && go test ./... >/dev/null) && echo "ok: notification-service tests"

echo
echo "matrix notify demo: ALL GREEN"
