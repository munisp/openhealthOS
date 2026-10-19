#!/bin/sh
# demo/matrix-stepup.sh — step-up + random re-authentication at the PHI
# boundary. A patient on the Matrix channel can browse freely, but reading
# the clinical record requires a fresh code delivered into their OWN matrix
# room; wrong codes 404; codes burn; and even a valid token gets bounced at
# random (STEPPUP_RANDOM_PERCENT), so a stolen session is never reliable.
set -eu
cd "$(dirname "$0")/.."
: > /tmp/mx-events.jsonl

cat > /tmp/mxs-stub.py <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[2]          # "hs" (homeserver), "pl" (platform: coverage/shr/tele)
SENT = []

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
                            evs.append({"event_id": d["id"], "sender": "@amina:hs",
                                        "type": "m.room.message",
                                        "content": {"msgtype": "m.text", "body": d["body"]}})
                rooms = {"!room1:hs": {"timeline": {"events": evs}}} if evs else {}
                return self._j({"next_batch": "n1", "rooms": {"join": rooms}})
            return self._j({}, 404)
        # platform
        if p == "/portal/coverage":
            if "OH-KD-0001" in self.path and "2348011112222" in self.path:
                return self._j({"status": "eligible"})
            return self._j({"error": "not found"}, 404)
        if p == "/fhir/Patient/OH-KD-0001/$everything":
            return self._j({"resourceType": "Bundle", "type": "searchset", "total": 1,
                            "entry": [{"resource": {"resourceType": "Observation", "id": "o1"}}]})
        if p == "/consults":
            return self._j({"consults": []})
        return self._j({}, 404)

    def do_PUT(self):
        if "/send/m.room.message/" in self.path:
            SENT.append(json.loads(self.rfile.read(int(self.headers["Content-Length"])))["body"])
            return self._j({"event_id": "$ok"})
        return self._j({}, 404)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._j({"status": "ok"})

    def log_message(self, *a): pass

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
PY

python3 /tmp/mxs-stub.py 18321 hs & HS_PID=$!
python3 /tmp/mxs-stub.py 18320 pl & PL_PID=$!
(cd services/whatsapp-gateway && go build -o /tmp/wa-gateway .)
(cd services/notification-service && go build -o /tmp/ntf .)

LISTEN=":18322" REGISTRY_URL=http://127.0.0.1:18320 \
  PORTAL_URL=http://127.0.0.1:18320 WELLNESS_URL=http://127.0.0.1:18320 \
  MATRIX_HOMESERVER=http://127.0.0.1:18321 MATRIX_ACCESS_TOKEN=tok \
  /tmp/wa-gateway >/tmp/mxs-gw.log 2>&1 & WA_PID=$!

LISTEN=":18323" SMS_PROVIDERS="matrix:http://127.0.0.1:18322,log" \
  MATRIX_HOMESERVER=http://127.0.0.1:18321 MATRIX_ACCESS_TOKEN=tok \
  SHR_URL=http://127.0.0.1:18320 TELEHEALTH_URL=http://127.0.0.1:18320 \
  /tmp/ntf >/tmp/mxs-ntf.log 2>&1 & NTF_PID=$!
trap 'kill $HS_PID $PL_PID $WA_PID $NTF_PID 2>/dev/null || true' EXIT
sleep 2

sent_count() { curl -s http://127.0.0.1:18321/control/sent | python3 -c "import json,sys;print(len(json.load(sys.stdin)['sent']))"; }
say() {
  n=$(sent_count)
  python3 -c "import json,sys;open('/tmp/mx-events.jsonl','a').write(json.dumps({'id':sys.argv[1],'body':sys.argv[2]})+'\n')" "$1" "$2"
  i=0; while [ $i -lt 50 ]; do [ "$(sent_count)" -gt "$n" ] && return 0; sleep 0.5; i=$((i+1)); done
}

echo "== patient binds on matrix, subscribes on the rail"
say m1 hi
say m2 "OH-KD-0001 08011112222"
curl -s http://127.0.0.1:18321/control/sent | grep -q "Book an appointment" && echo "ok: bound" || { echo "FAIL: bind"; exit 1; }
curl -sf -X POST http://127.0.0.1:18323/subscriptions -H 'Content-Type: application/json' \
  -d '{"patient_id":"OH-KD-0001","phone":"08011112222","facility_id":"FAC-1"}' >/dev/null && echo "ok: subscribed"

echo "== PHI read without elevation -> 428 step_up_required"
CODE=$(curl -s -o /tmp/mxs-r1.json -w '%{http_code}' "http://127.0.0.1:18323/portal/record?patient_id=OH-KD-0001&phone=08011112222")
[ "$CODE" = "428" ] && grep -q step_up_required /tmp/mxs-r1.json && echo "ok: 428, not record" || { echo "FAIL: got $CODE $(cat /tmp/mxs-r1.json)"; exit 1; }

echo "== wrong identity pair still gets the bare 404"
CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:18323/portal/record?patient_id=OH-KD-0001&phone=08099999999")
[ "$CODE" = "404" ] && echo "ok: fail-closed 404" || { echo "FAIL: wrong pair got $CODE"; exit 1; }

echo "== challenge: code delivered into the patient's OWN matrix room"
n=$(sent_count)
curl -sf -X POST http://127.0.0.1:18323/portal/stepup/challenge -H 'Content-Type: application/json' \
  -d '{"patient_id":"OH-KD-0001","phone":"08011112222"}' >/dev/null
i=0; while [ $i -lt 20 ]; do [ "$(sent_count)" -gt "$n" ] && break; sleep 0.5; i=$((i+1)); done
CODE6=$(curl -s http://127.0.0.1:18321/control/sent | python3 -c "
import json,sys,re
for m in json.load(sys.stdin)['sent']:
    g = re.search(r'security code: (\d{6})', m)
    if g: print(g.group(1)); break")
[ -n "$CODE6" ] && echo "ok: 6-digit code rode the matrix leg into the room" || { echo "FAIL: no code delivered"; exit 1; }

echo "== verify: wrong code 404, right code elevates, replay burns"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18323/portal/stepup/verify \
  -H 'Content-Type: application/json' -d '{"patient_id":"OH-KD-0001","phone":"08011112222","code":"000000"}')
[ "$CODE" = "404" ] && echo "ok: wrong code -> 404" || { echo "FAIL: wrong code got $CODE"; exit 1; }
TOK=$(curl -sf -X POST http://127.0.0.1:18323/portal/stepup/verify -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"OH-KD-0001\",\"phone\":\"08011112222\",\"code\":\"$CODE6\"}" \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['stepup_token'])")
[ -n "$TOK" ] && echo "ok: elevated token issued (10 min)" || { echo "FAIL: verify"; exit 1; }
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:18323/portal/stepup/verify \
  -H 'Content-Type: application/json' -d "{\"patient_id\":\"OH-KD-0001\",\"phone\":\"08011112222\",\"code\":\"$CODE6\"}")
[ "$CODE" = "404" ] && echo "ok: replayed code burned" || { echo "FAIL: replay got $CODE"; exit 1; }

echo "== elevated read renders the record (PHI flows only after step-up)"
curl -sf -H "X-StepUp-Token: $TOK" \
  "http://127.0.0.1:18323/portal/record?patient_id=OH-KD-0001&phone=08011112222" \
  | grep -q '"total":1' && echo "ok: record rendered with X-Patient-Self basis" || { echo "FAIL: elevated read"; exit 1; }

echo "== random re-auth: the ground moves under a held session (15% default)"
BOUNCED=0
i=0
while [ $i -lt 60 ]; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "X-StepUp-Token: $TOK" \
    "http://127.0.0.1:18323/portal/record?patient_id=OH-KD-0001&phone=08011112222")
  [ "$CODE" = "428" ] && { BOUNCED=1; break; }
  i=$((i+1))
done
[ "$BOUNCED" = "1" ] && echo "ok: valid token bounced for fresh challenge after $i clean reads" \
  || { echo "FAIL: no random bounce in 60 reads"; exit 1; }

echo "== unit battery"
(cd services/notification-service && go test ./... >/dev/null) && echo "ok: notification-service tests"

echo
echo "matrix step-up demo: ALL GREEN"
