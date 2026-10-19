#!/bin/sh
# demo/matrix.sh — ADR-0038 e2e: the sovereign channel. A patient binds
# (patient ID + phone), books an appointment, checks coverage, and
# switches language over a self-hosted Matrix transport — against a
# fake homeserver (file-backed /sync inbox, /control/sent outbox).
# No Meta, no internet, nothing exposed.
set -eu
cd "$(dirname "$0")/.."
: > /tmp/mx-events.jsonl

cat > /tmp/mx-stub.py <<'PY'
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODE = sys.argv[2]          # "hs" (homeserver) or "pl" (platform)
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
                    # atomic take: rename first so appends during the read survive
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
        # platform stub
        if p == "/patients/search":
            return self._j({"results": []})
        if p == "/portal/coverage":
            if "OH-KD-0001" in self.path and "2348011112222" in self.path:
                return self._j({"status": "eligible", "scheme": "NHIA Basic"})
            return self._j({"error": "not found"}, 404)
        if p == "/portal/facilities":
            return self._j({"facilities": [{"id": "FAC-1", "name": "Kawo PHC"}]})
        if p == "/portal/slots":
            return self._j({"slots": [{"id": "S1:480", "start": "2026-10-20T08:00:00Z",
                                       "capacity": 4, "booked": 1}]})
        if p == "/portal/appointments":
            return self._j({"appointments": []})
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

python3 /tmp/mx-stub.py 18311 hs & HS_PID=$!
python3 /tmp/mx-stub.py 18310 pl & PL_PID=$!
(cd services/whatsapp-gateway && go build -o /tmp/wa-gateway .)
LISTEN=":18312" REGISTRY_URL=http://127.0.0.1:18310 \
  PORTAL_URL=http://127.0.0.1:18310 WELLNESS_URL=http://127.0.0.1:18310 \
  MATRIX_HOMESERVER=http://127.0.0.1:18311 MATRIX_ACCESS_TOKEN=tok \
  /tmp/wa-gateway >/tmp/mx.log 2>&1 & WA_PID=$!
trap 'kill $HS_PID $PL_PID $WA_PID 2>/dev/null || true' EXIT
sleep 2

sent_count() { curl -s http://127.0.0.1:18311/control/sent | python3 -c "import json,sys;print(len(json.load(sys.stdin)['sent']))"; }
wait_reply() { # prev_count → block until a new outbound message lands (max 25s)
  i=0
  while [ $i -lt 50 ]; do
    [ "$(sent_count)" -gt "$1" ] && return 0
    sleep 0.5; i=$((i+1))
  done
  return 0 # let the check report the failure with context
}
say() { # id, body → inject one inbound event, wait for the reply
  n=$(sent_count)
  python3 -c "import json,sys;open('/tmp/mx-events.jsonl','a').write(json.dumps({'id':sys.argv[1],'body':sys.argv[2]})+'\n')" "$1" "$2"
  wait_reply "$n"
}
sayas() { # id, sender, body → a different matrix user
  n=$(sent_count)
  python3 -c "import json,sys;open('/tmp/mx-events.jsonl','a').write(json.dumps({'id':sys.argv[1],'sender':sys.argv[2],'body':sys.argv[3]})+'\n')" "$1" "$2" "$3"
  wait_reply "$n"
}
check() {
  curl -s http://127.0.0.1:18311/control/sent | grep -q "$1" \
    && echo "ok: $2" || { echo "FAIL: $2 — $(curl -s http://127.0.0.1:18311/control/sent)"; exit 1; }
}

echo "== sovereign channel: bind, book, coverage, language"
say m1 hi;                       check "patient ID" "bind: asks patient ID"
say m2 "OH-KD-0001 08011112222"; check "Book an appointment" "bind: one-message binding → menu"
say m3 1;                        check "Kawo PHC" "booking: facilities"
say m4 1;                        check "Which day" "booking: days"
say m5 1;                        check "Free times" "booking: slots"
say m6 1;                        check "Book Kawo PHC" "booking: confirm"
say m7 1;                        check "Booked:" "booking: booked"
say m8 3;                        check "covered" "coverage over matrix"
say m9 harshe;                   check "Hausa" "language picker on matrix"
say m10 3;                       check "Yi rajistar" "menu in Hausa"

echo "== wrong pair fails closed"
say w1 "english"
sayas w2 "@bob:hs" "hi"
sayas w3 "@bob:hs" "OH-KD-0001 08099999999"
check "match any record" "wrong pair rejected, nothing leaked"

echo "== unit battery"
(cd services/whatsapp-gateway && go test ./... >/dev/null) && echo "ok: whatsapp-gateway tests"

echo
echo "matrix sovereign-channel demo: ALL GREEN"
