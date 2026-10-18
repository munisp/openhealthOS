#!/usr/bin/env bash
# Patient record smoke (ADR-0016) — the patient's own record on their own
# phone, end to end across three in-memory services (no Postgres needed):
#
#   SHR: FHIR resources for PT-REC-1  ─┐
#   telehealth consult thread          ├─▶ notification /portal/record
#   subscription (patient code+phone) ─┘    (self-access basis, merged)
#
# Asserts the happy path, the auth gate (wrong phone = 404, no
# enumeration), the SHR audit trail (basis patient-self-access), and
# honest degradation (dark SHR → complete=false, consults still served).
#
# Run from the repo root: bash demo/patient-record.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
SHR_PID=""
cleanup() {
  [ -n "$SHR_PID" ] && kill "$SHR_PID" 2>/dev/null || true
  kill $(jobs -p) 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "== build =="
( cd services/shr-service && go build -o "$TMP/shr" . )
( cd services/telehealth-service && go build -o "$TMP/telehealth" . )
( cd services/notification-service && go build -o "$TMP/notify" . )

LISTEN=:18085 "$TMP/shr" >"$TMP/shr.log" 2>&1 &
SHR_PID=$!
LISTEN=:18087 SHR_URL=http://127.0.0.1:18085 "$TMP/telehealth" >"$TMP/telehealth.log" 2>&1 &
LISTEN=:18089 SHR_URL=http://127.0.0.1:18085 TELEHEALTH_URL=http://127.0.0.1:18087 "$TMP/notify" >"$TMP/notify.log" 2>&1 &
sleep 1.5
for port in 18085 18087 18089; do
  curl -sf -m 3 "http://127.0.0.1:$port/healthz" >/dev/null || {
    echo "service on $port failed to start:"; cat "$TMP"/*.log; exit 1; }
done

J='Content-Type: application/json'
PT="PT-REC-$RANDOM"          # hermetic per run — services are in-memory
PHONE="+2348031112222"
FAC="PHC-KAWO-REC"
say() { echo; echo "── $*"; }

say "1. patient subscribes with phone (the portal credential)"
curl -sf -X POST http://127.0.0.1:18089/subscriptions -H "$J" \
  -d "{\"patient_id\":\"$PT\",\"phone\":\"$PHONE\",\"language\":\"en\",\"facility\":\"$FAC\"}" >/dev/null && echo "   subscribed $PT"

say "2. SHR holds the patient's clinical record"
put_fhir() { # type id body
  python3 - "$1" "$2" "$3" "$PT" "$FAC" <<'PY' | curl -sf -X PUT "http://127.0.0.1:18085/fhir/$1/$2" -H "$J" -H "X-Facility-ID: $FAC" -d @- >/dev/null
import json, sys
rt, rid, body, pt, fac = sys.argv[1:6]
print(json.dumps({"resourceType": rt, "id": rid, "patient_id": pt,
                  "source_facility": fac, "body": json.loads(body)}))
PY
}
put_fhir Observation obs-rec-1 '{"resourceType":"Observation","code":{"text":"Haemoglobin"},"valueQuantity":{"value":11.2,"unit":"g/dL"},"effectiveDateTime":"2026-10-11T10:00:00Z"}'
put_fhir Condition cond-rec-1 '{"resourceType":"Condition","code":{"text":"Uncomplicated malaria"},"recordedDate":"2026-10-10"}'
put_fhir Immunization imm-rec-1 '{"resourceType":"Immunization","vaccineCode":{"text":"Measles vaccine"},"status":"completed","occurrenceDateTime":"2026-09-01T11:00:00Z"}'
echo "   observation + condition + immunization stored"

say "3. telehealth consult runs and closes (writes its Encounter to the SHR)"
CID=$(curl -sf -X POST http://127.0.0.1:18087/consults -H "$J" \
  -d "{\"patient_id\":\"$PT\",\"facility_id\":\"$FAC\",\"nurse_id\":\"N-1\",\"complaint\":\"fever x 2 days\"}" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf -X POST "http://127.0.0.1:18087/consults/$CID/assess" -H "$J" \
  -d '{"protocol":"IMCI fever","assessment":"RDT positive, no danger signs"}' >/dev/null
curl -sf -X POST "http://127.0.0.1:18087/consults/$CID/close" >/dev/null && echo "   consult $CID closed"

say "4. the phone fetches the merged record"
REC=$(curl -sf "http://127.0.0.1:18089/portal/record?patient_id=$PT&phone=%2B2348031112222")
python3 - "$PT" "$REC" <<'PY'
import json, sys
pt = sys.argv[1]
rec = json.loads(sys.argv[2])
assert rec["patient_id"] == pt, rec
assert rec["complete"] is True, f"expected complete record: {rec}"
bundle = rec["record"]
types = sorted(e["resource"]["resourceType"] for e in bundle["entry"])
assert types == ["Condition", "Encounter", "Immunization", "Observation"], types
consults = rec["consults"]
assert isinstance(consults, list) and len(consults) == 1, consults
assert consults[0]["complaint"] == "fever x 2 days", consults
print(f"   complete=true · bundle: {types} · consults: 1 (fever x 2 days)")
PY

say "5. the SHR audited the self-access with its own basis"
AUD=$(curl -sf http://127.0.0.1:18085/audit)
python3 - "$PT" "$AUD" <<'PY'
import json, sys
pt = sys.argv[1]
audit = json.loads(sys.argv[2])
hits = [a for a in audit if a.get("basis") == "patient-self-access" and a.get("patient_id") == pt]
assert hits and hits[-1]["actor"] == f"portal:{pt}" and hits[-1]["decision"] == "allowed", audit
print(f"   basis=patient-self-access actor=portal:{pt} decision=allowed")
PY

say "6. wrong phone sees 404 — same as unknown patient (no enumeration)"
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://127.0.0.1:18089/portal/record?patient_id=$PT&phone=%2B2348099999999")
[ "$CODE" = "404" ] || { echo "   want 404, got $CODE"; exit 1; }
CODE=$(curl -s -o /dev/null -w "%{http_code}" \
  "http://127.0.0.1:18089/portal/record?patient_id=PT-NOBODY&phone=%2B2348031112222")
[ "$CODE" = "404" ] || { echo "   want 404, got $CODE"; exit 1; }
echo "   wrong phone → 404 · unknown patient → 404"

say "7. dark SHR degrades honestly — the phone is never 500'd"
kill "$SHR_PID"; wait "$SHR_PID" 2>/dev/null || true; SHR_PID=""
REC=$(curl -sf "http://127.0.0.1:18089/portal/record?patient_id=$PT&phone=%2B2348031112222")
python3 - "$REC" <<'PY'
import json, sys
rec = json.loads(sys.argv[1])
assert rec["complete"] is False, f"dark SHR must flag incomplete: {rec}"
assert rec["record"] is None, f"failed leg must be explicit null: {rec['record']!r}"
assert isinstance(rec["consults"], list) and len(rec["consults"]) == 1, rec["consults"]
print("   complete=false · record=null · consults still served")
PY

echo; echo "== patient record smoke: all assertions passed =="
