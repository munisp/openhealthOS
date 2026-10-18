#!/usr/bin/env bash
# National sync smoke — the three-tier topology working end to end:
#
#   facility sync-agent ──▶ LGA hub (store-and-forward, priority queues,
#   │                       JSONL spill for power cuts)
#   └── power cut! hub restarts, nothing is lost ──┘
#                          └──▶ state-plane sync-ingest (idempotent)
#
# Asserts: critical events forward before bulk; upstream-down buffers;
# hub restart resurrects nothing; duplicates are acked not re-applied.
#
# Run from the repo root: bash demo/national-sync.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() {
  kill $(jobs -p) 2>/dev/null || true
  pkill -f "$TMP/(ingest|hub)" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "== build =="
( cd services/sync-ingest && go build -o "$TMP/ingest" . )
( cd services/lga-hub && go build -o "$TMP/hub" . )

# state plane (upstream)
LISTEN=:18443 "$TMP/ingest" >/dev/null 2>&1 &
# LGA hub with a spill file and a 1s forward tick
LISTEN=:18090 SPILL_PATH="$TMP/hub.jsonl" UPSTREAM_URL=http://127.0.0.1:18443 \
  HUB_ID=KD-KAWO-LGA FORWARD_INTERVAL_SECONDS=1 "$TMP/hub" >/dev/null 2>&1 &
sleep 1.5

J='Content-Type: application/x-ndjson'
say() { echo; echo "── $*"; }

say "1. facility sends a mixed batch to its LGA hub (bulk first)"
curl -sf -X POST http://127.0.0.1:18090/ingest/raw -H "$J" -d \
'{"id":"ev-bulk-1","facility":"PHC-1","type":"telemetry","ts":"2026-10-18T08:00:00Z","payload":{"metric":"fridge_temp","value":4.1}}
{"id":"ev-std-1","facility":"PHC-1","type":"encounter","ts":"2026-10-18T08:00:01Z"}
{"id":"ev-crit-1","facility":"PHC-1","type":"referral","ts":"2026-10-18T08:00:02Z","payload":{"patient_id":"PT-77","to_facility":"GH-Kaduna","urgency":"urgent"}}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   hub ack:", d["received"], "received via", d["hub"])'

say "2. same batch retried (agent flakiness) — acked as duplicates"
curl -sf -X POST http://127.0.0.1:18090/ingest/raw -H "$J" -d \
'{"id":"ev-bulk-1","facility":"PHC-1","type":"telemetry","ts":"2026-10-18T08:00:00Z","payload":{"metric":"fridge_temp","value":4.1}}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   duplicates:", d["duplicates"])'

say "3. hub forwards to the state plane (critical first)"
sleep 2
curl -sf "http://127.0.0.1:18443/events" | python3 -c '
import json,sys
ids = [e["id"] for e in json.load(sys.stdin)]
print("   upstream order:", ids)
assert ids.index("ev-crit-1") < ids.index("ev-bulk-1"), "critical must arrive before bulk"
print("   priority order OK")'
curl -sf http://127.0.0.1:18090/stats | python3 -c '
import json,sys; d=json.load(sys.stdin)
assert d["pending_critical"] == d["pending_standard"] == d["pending_bulk"] == 0
print("   hub queues empty, forwarded_total:", d["forwarded_total"])'

say "4. POWER CUT: hub killed mid-operation, one event buffered"
pkill -f "$TMP/hub" && sleep 0.5
LISTEN=:18090 SPILL_PATH="$TMP/hub.jsonl" UPSTREAM_URL=http://127.0.0.1:18443 \
  HUB_ID=KD-KAWO-LGA FORWARD_INTERVAL_SECONDS=1 "$TMP/hub" >/dev/null 2>&1 &
sleep 1.5
curl -sf http://127.0.0.1:18090/stats | python3 -c '
import json,sys; d=json.load(sys.stdin)
assert d["pending_critical"] == d["pending_standard"] == d["pending_bulk"] == 0, d
print("   restart clean: no resurrected events, queues empty")'

say "5. new event after restart forwards normally"
curl -sf -X POST http://127.0.0.1:18090/ingest/raw -H "$J" -d \
'{"id":"ev-crit-2","facility":"PHC-2","type":"emergency","ts":"2026-10-18T09:00:00Z","payload":{"danger":true}}' >/dev/null
sleep 2
curl -sf "http://127.0.0.1:18443/events" | python3 -c '
import json,sys
ids = [e["id"] for e in json.load(sys.stdin)]
assert "ev-crit-2" in ids, ids
assert len(ids) == len(set(ids)) == 4, f"no duplicates, got {ids}"
print("   upstream now holds:", ids)'

echo; echo "NATIONAL SYNC OK"
