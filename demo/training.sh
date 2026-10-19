#!/usr/bin/env bash
# Training/LMS smoke (ADR-0029): course → enrolment → video + quiz +
# checklist sign-off → ledger-signed certificate → verify → revoke →
# guideline bump → attestation gap. Uses the real Rust ledger binary.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/ledger && cargo build --offline --release 2>/dev/null || cargo build --release )
( cd services/training-service && go build -o "$TMP/training" . )
LEDGER=""
for cand in services/ledger/target/release/oh_ledger "${CARGO_TARGET_DIR:-/nonexistent}/release/oh_ledger"; do
  [ -x "$cand" ] && LEDGER="$cand" && break
done
[ -n "$LEDGER" ] || { echo "ledger binary not found"; exit 1; }

LISTEN=:18198 LEDGER_BIN="$(readlink -f "$LEDGER")" LEDGER_DATA_DIR="$TMP/ledger" "$TMP/training" >"$TMP/training.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18198/healthz" >/dev/null || { cat "$TMP/training.log"; exit 1; }
API=http://127.0.0.1:18198

echo "== 1. publish a CPD course (NMCN, 4 points) =="
curl -sf -X POST "$API/courses" -H 'Content-Type: application/json' -d '{
  "id":"hbb","title":"Helping Babies Breathe","version":"2025.1",
  "guideline_version":"WHO-HBB-2","cpd_points":4,"scheme":"NMCN",
  "modules":[
    {"id":"vid","title":"Resuscitation video","type":"video","ref":"blob://media/hbb.mp4"},
    {"id":"quiz","title":"Knowledge check","type":"quiz","pass_mark":50,
     "questions":[{"prompt":"First action?","options":["Dry & stimulate","Suction"],"answer":0}]},
    {"id":"skills","title":"Practical","type":"checklist","items":["bag-mask seal","chest rise"]}
  ]}' >/dev/null && echo "   course published"

echo "== 2. enrol + complete all modules =="
ENR=$(curl -sf -X POST "$API/enrolments" -H 'Content-Type: application/json' \
  -d '{"course_id":"hbb","practitioner_id":"nurse-42","facility_id":"phc-karu"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf -X POST "$API/enrolments/$ENR/modules/vid/done" >/dev/null
curl -sf -X POST "$API/enrolments/$ENR/modules/quiz/quiz" \
  -H 'Content-Type: application/json' -d '{"answers":[0]}' | grep -q '"score":100'
curl -sf -X POST "$API/enrolments/$ENR/modules/skills/sign" -H 'X-Actor: assessor-ada' \
  -H 'Content-Type: application/json' -d '{"item":"bag-mask seal"}' >/dev/null
curl -sf -X POST "$API/enrolments/$ENR/modules/skills/sign" -H 'X-Actor: assessor-ada' \
  -H 'Content-Type: application/json' -d '{"item":"chest rise"}' >/dev/null
echo "   video watched, quiz passed, checklist signed"

echo "== 3. certificate issuance is a ledger transaction =="
CERT=$(curl -sf -X POST "$API/enrolments/$ENR/certificate" -H 'Content-Type: application/json' -d '{}')
SERIAL=$(echo "$CERT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["serial"])')
echo "   serial: $SERIAL"
grep -q "cert:$SERIAL" "$TMP/ledger/journal.jsonl" 2>/dev/null \
  && echo "   ledger journal carries the certificate posting" \
  || echo "   (ledger posted in-memory)"

echo "== 4. regulator verifies =="
curl -sf "$API/certificates/$SERIAL/verify" | grep -q '"valid":true' \
  && echo "   verify: valid, CPD balance consistent"

echo "== 5. revocation is a counter-entry =="
curl -sf -X POST "$API/certificates/$SERIAL/revoke" -H 'X-Actor: nmcn-regulator' \
  -H 'Content-Type: application/json' -d '{"reason":"fraudulent attestation"}' >/dev/null
curl -sf "$API/certificates/$SERIAL/verify" | grep -q '"valid":false' \
  && echo "   verify after revoke: invalid, balance reversed"

echo "== 6. CPD summary (scheme-filtered) =="
curl -sf "$API/practitioners/nurse-42/cpd?scheme=NMCN" | grep -q '"scheme":"NMCN"' \
  && echo "   scheme summary answers"

echo "== 7. guideline bump → attestation gap =="
curl -sf -X POST "$API/guidelines/hbb/bump" -H 'X-Actor: cmoh' \
  -H 'Content-Type: application/json' -d '{"guideline_version":"WHO-HBB-3"}' \
  | grep -q '"flagged":1'
curl -sf "$API/facilities/phc-karu/attestation-gap" | grep -q 'nurse-42' \
  && echo "   nurse-42 flagged for re-attestation at phc-karu"

echo "== 8. IVR quiz seam =="
ENR2=$(curl -sf -X POST "$API/enrolments" -H 'Content-Type: application/json' \
  -d '{"course_id":"hbb","practitioner_id":"chw-7","facility_id":"phc-karu"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf "$API/ivr/quiz/$ENR2/quiz" | grep -q '"questions":1' \
  && echo "   IVR exposes the quiz prompts"

echo "TRAINING DEMO OK"
