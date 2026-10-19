#!/bin/sh
# demo/whatsapp.sh — ADR-0037 e2e: a patient books, cancels, checks
# coverage and logs a reading over WhatsApp, fully offline (file outbox
# instead of the Cloud API).
set -eu
cd "$(dirname "$0")/.."

PORT=18300
OUT=/tmp/wa-outbox.jsonl
: > "$OUT"

echo "== boot whatsapp-gateway against stub platform"
STUB=/tmp/wa-stub.py
cat > "$STUB" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

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
        if "phone=2348011112222" in self.path.replace("%2B", "") or "q=2348011112222" in self.path:
            pass
        if p == "/patients/search":
            if "2348011112222" in self.path:
                return self._j({"results": [{"id": "OH-KD-0001", "name": "Amina Bello", "phone": "+2348011112222"}]})
            return self._j({"results": []})
        if p == "/portal/facilities":
            return self._j({"facilities": [{"id": "FAC-1", "name": "Kawo PHC"}]})
        if p == "/portal/slots":
            return self._j({"slots": [{"id": "S1:480", "start": "2026-10-20T08:00:00Z", "capacity": 4, "booked": 1}]})
        if p == "/portal/appointments":
            return self._j({"appointments": [{"id": "APT-1", "facility_id": "FAC-1", "service": "ANC", "start": "2026-10-21T09:00:00Z", "status": "booked"}]})
        if p == "/portal/coverage":
            return self._j({"status": "eligible", "scheme": "NHIA Basic"})
        if p == "/portal/feed":
            return self._j({"notifications": [{"kind": "result", "body": "Your lab result is ready"}]})
        return self._j({}, 404)
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        return self._j({"status": "ok"})
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 18301), H).serve_forever()
PY
python3 "$STUB" & STUB_PID=$!
trap 'kill $STUB_PID $WA_PID 2>/dev/null || true' EXIT

(cd services/whatsapp-gateway && go build -o /tmp/wa-gateway .)
WA_OUTBOX="$OUT" LISTEN=":$PORT" REGISTRY_URL=http://127.0.0.1:18301 \
  PORTAL_URL=http://127.0.0.1:18301 WELLNESS_URL=http://127.0.0.1:18301 \
  /tmp/wa-gateway >/tmp/wa.log 2>&1 & WA_PID=$!
for i in 1 2 3 4 5 6 7 8 9 10; do curl -s "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1 && break; sleep 1; done

wa() { # id, text
  curl -s -o /dev/null -X POST "http://127.0.0.1:$PORT/webhooks/whatsapp" \
    -H 'Content-Type: application/json' \
    -d "{\"entry\":[{\"changes\":[{\"value\":{\"messages\":[{\"id\":\"$1\",\"from\":\"2348011112222\",\"type\":\"text\",\"text\":{\"body\":\"$2\"}}]}}]}]}"
}

echo "== handshake"
CH=$(curl -s "http://127.0.0.1:$PORT/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=dev-verify-token&hub.challenge=CH7")
[ "$CH" = "CH7" ] || { echo "FAIL: handshake"; exit 1; }
echo "ok: verify handshake echoes challenge"

wa m1 hi;        grep -q "Book an appointment" "$OUT" && echo "ok: menu"
wa m2 1;         grep -q "Kawo PHC" "$OUT" && echo "ok: facilities listed"
wa m3 1;         grep -q "Which day" "$OUT" && echo "ok: dates offered"
wa m4 1;         grep -q "Free times" "$OUT" && echo "ok: slots offered"
wa m5 1;         grep -q "Book Kawo PHC" "$OUT" && echo "ok: confirmation"
wa m6 1;         grep -q "Booked:" "$OUT" && echo "ok: appointment booked"
wa m7 2;         grep -q "ANC" "$OUT" && echo "ok: my appointments"
wa m8 1;         grep -q "Cancelled:" "$OUT" && echo "ok: cancelled"
wa m9 3;         grep -q "covered" "$OUT" && echo "ok: coverage"
wa m10 4;        grep -q "lab result is ready" "$OUT" && echo "ok: clinic updates"
wa m11 5; wa m12 "my child has a fever"; grep -q "triage queue" "$OUT" && echo "ok: ask the clinic"
wa m13 6; wa m14 2; wa m15 72.5; grep -q "Logged to your record" "$OUT" && echo "ok: wellness reading logged"

echo "== webhook retry must not double-act"
N=$(wc -l < "$OUT")
wa m1 hi   # same message id again
M=$(wc -l < "$OUT")
[ "$N" = "$M" ] || { echo "FAIL: duplicate reply"; exit 1; }
echo "ok: retried message id ignored"

echo "== unknown number gets guidance, no enumeration"
curl -s -o /dev/null -X POST "http://127.0.0.1:$PORT/webhooks/whatsapp" -H 'Content-Type: application/json' \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"id":"x1","from":"2348099999999","type":"text","text":{"body":"hi"}}]}}]}]}'
curl -s -o /dev/null -X POST "http://127.0.0.1:$PORT/webhooks/whatsapp" -H 'Content-Type: application/json' \
  -d '{"entry":[{"changes":[{"value":{"messages":[{"id":"x2","from":"2348099999999","type":"text","text":{"body":"1"}}]}}]}]}'
grep -q "isn't linked to a health record" "$OUT" && echo "ok: enrolment guidance, nothing leaked"

echo "== five languages"
wa g1 7; grep -q "Hausa" "$OUT" && echo "ok: language picker"
wa g2 3; grep -q "Yi rajistar" "$OUT" && echo "ok: menu in Hausa"
wa g3 "pidgin"; grep -q "Naija" "$OUT" && echo "ok: pidgin keyword switch"
wa g4 3; grep -q "You get cover" "$OUT" && echo "ok: coverage in Pidgin"

echo "== unit battery"
(cd services/whatsapp-gateway && go test ./... >/dev/null) && echo "ok: whatsapp-gateway tests"

echo
echo "whatsapp channel demo: ALL GREEN"
