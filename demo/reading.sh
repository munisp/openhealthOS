#!/usr/bin/env bash
# Reading-service smoke: register a reader, order a STAT case, assign,
# worklist (STAT-first), draft → verify (critical finding), escalation
# ack ladder, turnaround stats — over real HTTP, with file persistence.
# Then the reporting plane: versioned structured templates, validated
# drafts, HMAC-signed reports (the hash is the contract), FHIR + printable
# HTML export, and viewer launch (OHIF) with fail-loud refusals.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
SRV=""
cleanup() { [ -n "$SRV" ] && kill "$SRV" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/reading-service && go build -o "$TMP/reading" . )

# reportkit + viewlink envs propagate to the Python engines the service
# shells out to; SLIDE_BASE/ECG_BASE deliberately unset to prove fail-loud.
start_server() {
  env LISTEN=:18101 \
      TELEREAD_CLI=packages/teleread/cli.py TELEREAD_DB="$TMP/state.json" \
      REPORTKIT_CLI=packages/reportkit/cli.py REPORTKIT_DB="$TMP/rk.json" \
      VIEWLINK_CLI=packages/viewlink/cli.py VIEWLINK_DB="$TMP/vl.json" \
      REPORTKIT_SIGNING_KEY=demo-signing-key \
      OHIF_BASE=http://viewer.example/ohif \
      "$TMP/reading" >>"$TMP/reading.log" 2>&1 &
  SRV=$!
}
start_server
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
kill "$SRV" 2>/dev/null || true
sleep 0.3
start_server
sleep 1
curl -sf "$B/turnaround?domain=radiology&day=1" | grep -q '"n": 1'

echo "== structured templates are data, versioned =="
curl -sf "$B/report-templates" | grep -q 'ecg-12lead@1.0.0'
curl -sf "$B/report-template?template_id=ecg-12lead@1.0.0" | grep -q '"qtc_ms"'

echo "== structured draft validated against the template =="
curl -sf -X POST "$B/reports/structured" -d '{"report_id":"S1-ecg","template_id":"ecg-12lead@1.0.0","case_id":"S1","author_id":"rad-1","day":1,"answers":{"rate_bpm":88,"rhythm":"sinus","pr_ms":160,"qrs_ms":92,"qtc_ms":410,"findings":"normal intervals","interpretation":"normal sinus rhythm","acuity":"routine"}}' | grep -q '"status": "draft"'

echo "== fail-closed: coded field outside the set is refused =="
code=$(curl -s -o "$TMP/err2.json" -w '%{http_code}' -X POST "$B/reports/structured" -d '{"report_id":"S1-bad","template_id":"ecg-12lead@1.0.0","case_id":"S1","author_id":"rad-1","day":1,"answers":{"rate_bpm":88,"rhythm":"sinus","pr_ms":160,"qrs_ms":92,"qtc_ms":410,"findings":"x","interpretation":"x","acuity":"maybe"}}')
[ "$code" = 422 ] && grep -q "acuity" "$TMP/err2.json"

echo "== fail-closed: implausible number is refused =="
code=$(curl -s -o "$TMP/err3.json" -w '%{http_code}' -X POST "$B/reports/structured" -d '{"report_id":"S1-bad2","template_id":"ecg-12lead@1.0.0","case_id":"S1","author_id":"rad-1","day":1,"answers":{"rate_bpm":999,"rhythm":"sinus","pr_ms":160,"qrs_ms":92,"qtc_ms":410,"findings":"x","interpretation":"x","acuity":"routine"}}')
[ "$code" = 422 ] && grep -q "rate_bpm" "$TMP/err3.json"

echo "== sign → the hash is the contract; tamper-evident =="
curl -sf -X POST "$B/reports/sign" -d '{"report_id":"S1-ecg","signer_id":"rad-1","day":1}' | grep -q '"HMAC-SHA256"'
curl -sf "$B/reports/check?report_id=S1-ecg" | grep -q '"valid": true'

echo "== exports: FHIR DiagnosticReport + printable HTML =="
curl -sf "$B/reports/export?report_id=S1-ecg" | grep -q '"resourceType": "DiagnosticReport"'
curl -sf "$B/reports/export?report_id=S1-ecg&format=html" | grep -q 'S1-ecg'

echo "== viewer launch: worklist hands the reader pixels =="
curl -sf "$B/viewer/launch?kind=dicom-ohif&artifact_ref=1.2.3.4.5&reader_id=rad-1&session_id=sess-1&case_id=S1&day=1" | grep -q 'StudyInstanceUIDs=1.2.3.4.5'
curl -sf "$B/viewer/access-log?case_id=S1" | grep -q '"reader_id": "rad-1"'

echo "== fail-loud: unconfigured viewer kind refuses, no empty frame =="
code=$(curl -s -o "$TMP/err4.json" -w '%{http_code}' "$B/viewer/launch?kind=wsi-openseadragon&artifact_ref=slide-1&reader_id=rad-1&session_id=sess-1&case_id=S1&day=1")
[ "$code" = 422 ] && grep -q "not configured" "$TMP/err4.json"

echo "reading-service smoke: OK"
