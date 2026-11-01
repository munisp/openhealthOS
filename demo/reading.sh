#!/usr/bin/env bash
# Reading-service smoke: register a reader, order a STAT case, assign,
# worklist (STAT-first), draft → verify (critical finding), escalation
# ack ladder, turnaround stats — over real HTTP, with file persistence.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/reading-service && go build -o "$TMP/reading" . )

LISTEN=:18101 TELEREAD_CLI=packages/teleread/cli.py TELEREAD_DB="$TMP/state.json" "$TMP/reading" >"$TMP/reading.log" 2>&1 &
sleep 1
B=http://127.0.0.1:18101

echo "== register reader =="
curl -sf -X POST "$B/readers" -d '{"reader_id":"rad-1","licence_id":"MDCN-1","licence_expiry_day":99999,"scopes":["chest"]}' | grep -q '"licence"'

echo "== order a STAT chest case =="
curl -sf -X POST "$B/orders" -d '{"domain":"radiology","case_id":"S1","priority":"stat","question":"?pneumothorax","facility":"phc-1","referrer":"nurse-1","day":0,"scope":"chest","artifact":"dicom-study"}' | grep -q '"state": "unassigned"'

echo "== fail-closed: no question, no order =="
code=$(curl -s -o "$TMP/err.json" -w '%{http_code}' -X POST "$B/orders" -d '{"domain":"radiology","case_id":"S2","priority":"stat","question":"","facility":"f","referrer":"r","day":0}')
[ "$code" = 422 ] && grep -q "question" "$TMP/err.json"

echo "== assign (scope- and credential-aware) =="
curl -sf -X POST "$B/assign" -d '{"domain":"radiology","case_id":"S1","day":0}' | grep -q '"assigned_to": "rad-1"'

echo "== worklist is STAT-first =="
curl -sf "$B/worklist?domain=radiology&reader_id=rad-1&day=0" | grep -q '"case_id": "S1"'

echo "== reading session audited =="
curl -sf -X POST "$B/sessions" -d '{"domain":"radiology","reader_id":"rad-1","case_id":"S1","day":0,"location":"lagos","network":"offline"}' | grep -q '"location": "lagos"'

echo "== draft → verify; critical finding escalates =="
curl -sf -X POST "$B/reports/draft" -d '{"domain":"radiology","case_id":"S1","reader_id":"rad-1","findings":"right-sided pneumothorax","impression":"urgent decompression","day":0,"critical_findings":["pneumothorax"],"template_version":"cxr-v1"}' | grep -q '"state": "draft"'
curl -sf -X POST "$B/reports/verify" -d '{"domain":"radiology","case_id":"S1","reader_id":"rad-1","day":0}' | grep -q '"state": "verified"'
curl -sf "$B/critical/unack?domain=radiology&day=1" | grep -q pneumothorax
curl -sf -X POST "$B/critical/ack" -d '{"domain":"radiology","case_id":"S1","day":1,"by":"mo-1"}' | grep -q '"acknowledged"'
curl -sf "$B/critical/unack?domain=radiology&day=1" | grep -q '\[\]'

echo "== turnaround ships denominators =="
curl -sf "$B/turnaround?domain=radiology&day=1" | grep -q '"n": 1'

echo "== restart: state survives (file persistence) =="
kill $(jobs -p) 2>/dev/null || true
sleep 0.3
LISTEN=:18101 TELEREAD_CLI=packages/teleread/cli.py TELEREAD_DB="$TMP/state.json" "$TMP/reading" >>"$TMP/reading.log" 2>&1 &
sleep 1
curl -sf "$B/turnaround?domain=radiology&day=1" | grep -q '"n": 1'

echo "reading-service smoke: OK"
