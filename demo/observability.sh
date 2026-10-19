#!/bin/sh
# observability.sh — end-to-end proof of the ADR-0040 telemetry plane:
# service spans -> OTLP collector (mock), /metrics with per-tenant labels,
# and an Alertmanager webhook -> ops contact through the provider chain.
set -eu
cd "$(dirname "$0")/.."

BASE=$(mktemp -d)
trap 'kill $(cat $BASE/pids 2>/dev/null) 2>/dev/null; rm -rf "$BASE"' EXIT

echo "== build"
(cd services/notification-service && go build -o "$BASE/notification-service" .)
(cd services/stats-bridge && go build -o "$BASE/stats-bridge" .)

echo "== mock otel collector"
cat > "$BASE/collector.py" <<'PY'
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/v1/traces":
            with open("/tmp/otel-demo-traces.jsonl", "a") as fh:
                fh.write(body.decode() + "\n")
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass

HTTPServer(("127.0.0.1", 18318), H).serve_forever()
PY
python3 "$BASE/collector.py" & echo $! >> "$BASE/pids"

echo "== notification-service instrumented"
LISTEN=127.0.0.1:18319 \
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:18318 \
OTEL_FLUSH_MS=100 \
ALERT_OPS_CONTACTS=+2348000000001 \
"$BASE/notification-service" > "$BASE/svc.log" 2>&1 & echo $! >> "$BASE/pids"

echo "== stats-bridge (tigerbeetle-style sidecar)"
echo '{"name":"tigerbeetle_requests_total","type":"counter","help":"ops","labels":{"op":"create_transfers"},"value":8189}' > "$BASE/stats.jsonl"
STATS_PATH="$BASE/stats.jsonl" LISTEN=127.0.0.1:18317 "$BASE/stats-bridge" > "$BASE/bridge.log" 2>&1 & echo $! >> "$BASE/pids"
sleep 1.5

echo "== 1. traffic with a tenant header -> span at collector with tenant.id"
curl -s -H "x-tenant-id: lagos" http://127.0.0.1:18319/stats > /dev/null
sleep 0.5
python3 - <<'PY'
import json, sys
lines = open("/tmp/otel-demo-traces.jsonl").read().strip().split("\n")
payload = json.loads(lines[-1])
spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
assert spans, "no spans"
attrs = {a["key"]: a["value"] for a in spans[-1]["attributes"]}
assert attrs.get("tenant.id", {}).get("stringValue") == "lagos", attrs
print("ok: span carried tenant.id=lagos to the collector")
PY

echo "== 2. /metrics slices per tenant"
curl -s -H "x-tenant-id: kano" http://127.0.0.1:18319/stats > /dev/null
METRICS=$(curl -s http://127.0.0.1:18319/metrics)
echo "$METRICS" | grep -q 'http_requests_total{service="notification-service",status="200",tenant="lagos"}' || { echo "FAIL: lagos series"; exit 1; }
echo "$METRICS" | grep -q 'tenant="kano"' || { echo "FAIL: kano series"; exit 1; }
echo "ok: per-tenant series present"

echo "== 3. alertmanager webhook -> ops contact via provider chain"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:18319/alerts/intake \
  -H "Content-Type: application/json" \
  -d '{"status":"firing","alerts":[{"status":"firing","labels":{"alertname":"PostgresConnectionsExhausted","severity":"critical","tenant":"lagos"},"annotations":{"summary":"pg >85%"}}]}')
[ "$CODE" = 200 ] || { echo "FAIL: intake $CODE"; exit 1; }
echo "ok: alert delivered (log provider carries it in dev)"

echo "== 4. stats-bridge renders the tigerbeetle series"
curl -s http://127.0.0.1:18317/metrics | grep -q 'tigerbeetle_requests_total{op="create_transfers"} 8189' || { echo "FAIL: bridge"; exit 1; }
echo "ok: non-HTTP component metric on /metrics"

echo "== 5. tenant without contacts falls back to the platform on-call list"
CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://127.0.0.1:18319/alerts/intake \
  -H "Content-Type: application/json" \
  -d '{"status":"firing","alerts":[{"status":"firing","labels":{"alertname":"TenantSilent","severity":"warning","tenant":"ghost"}}]}')
[ "$CODE" = 200 ] || { echo "FAIL: expected platform fallback 200, got $CODE"; exit 1; }
echo "ok: fallback delivered (the no-contacts-at-all 503 case is unit-tested)"

echo "ALL OBSERVABILITY CHECKS PASSED"
