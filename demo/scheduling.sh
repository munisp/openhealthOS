#!/usr/bin/env bash
# Scheduling smoke (ADR-0017) — the patient self-booking rail, end to end
# across three in-memory services (no Postgres needed):
#
#   scheduling (slots, booking, waitlist) ─┐
#   claims orchestrator (eligibility)      ├─▶ notification /portal/*
#   subscription (patient code+phone)     ─┘   (self-service rail)
#
# Asserts: schedule → slots → self-book through the portal gate, the
# no-double-book invariant (409), waitlist promotion on cancel, the
# advisory coverage snapshot decorating the appointment, and the auth
# gate (wrong phone = 404, no enumeration).
#
# Run from the repo root: bash demo/scheduling.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/scheduling-service && go build -o "$TMP/sched" . )
( cd services/claims-orchestrator && go build -o "$TMP/claims" . )
( cd services/notification-service && go build -o "$TMP/notify" . )

LISTEN=:18080 MEMBERS_FILE="$TMP/members.json" \
  ADJUDICATOR="$PWD/packages/adjudicator/adjudicate.py" \
  "$TMP/claims" >"$TMP/claims.log" 2>&1 &
LISTEN=:18092 NOTIFY_URL=http://127.0.0.1:18089 ELIGIBILITY_URL=http://127.0.0.1:18080 \
  "$TMP/sched" >"$TMP/sched.log" 2>&1 &
LISTEN=:18089 SCHEDULING_URL=http://127.0.0.1:18092 ELIGIBILITY_URL=http://127.0.0.1:18080 \
  "$TMP/notify" >"$TMP/notify.log" 2>&1 &
sleep 1.5
for port in 18080 18089 18092; do
  curl -sf -m 3 "http://127.0.0.1:$port/healthz" >/dev/null || {
    echo "service on $port failed to start:"; cat "$TMP"/*.log; exit 1; }
done

SCHED=http://127.0.0.1:18092
PORTAL=http://127.0.0.1:18089

echo "== 1. facility publishes a Monday clinic, slots materialise =="
# Next Monday from today.
MONDAY=$(python3 -c "
import datetime
d = datetime.date.today() + datetime.timedelta(days=1)
while d.weekday() != 0: d += datetime.timedelta(days=1)
print(d.isoformat())")
SCHED_ID=$(curl -sf -X POST "$SCHED/schedules" -H 'Content-Type: application/json' -d '{
  "facility_id":"NG-KD-0014","resource_id":"DR-AMINA","resource_name":"Dr Amina",
  "resource_type":"provider","service":"anc","weekday":1,
  "start_min":480,"end_min":540,"slot_minutes":20,"capacity":1}' \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")
curl -sf -X POST "$SCHED/slots/generate" -H 'Content-Type: application/json' \
  -d "{\"schedule_id\":\"$SCHED_ID\",\"date\":\"$MONDAY\"}" >/dev/null
SLOTS=$(curl -sf "$SCHED/slots?facility_id=NG-KD-0014&service=anc&from=$MONDAY&to=${MONDAY}T23:59:59Z")
echo "$SLOTS" | python3 -c "
import json,sys
d = json.load(sys.stdin)
slots = d.get('slots', d if isinstance(d, list) else [])
assert len(slots) == 3, f'expected 3 slots, got {len(slots)}'
print('   3 slots materialised for', '$MONDAY')
print(slots[0]['id'])
" > "$TMP/slots.out"
head -1 "$TMP/slots.out"
SLOT1=$(tail -1 "$TMP/slots.out")

echo "== 1.5 patient is enrolled in BHCPF (coverage snapshot must be real) =="
curl -sf -X POST "http://127.0.0.1:18080/enrolments" -H 'Content-Type: application/json' -d '{
  "member_id":"PT-SCH-1","name":"Demo Patient","scheme":"BHCPF-VUL",
  "valid_until":"2027-12-31"}' | python3 -c "
import json,sys
m = json.load(sys.stdin)
assert m['scheme'] == 'BHCPF-VUL', m
print('   enrolled:', m['member_id'], 'under', m['scheme'])
"

echo "== 2. patient subscribes, then books through the portal gate =="
curl -sf -X POST "$PORTAL/subscriptions" -H 'Content-Type: application/json' \
  -d '{"patient_id":"PT-SCH-1","phone":"08030001111","language":"en","facility_id":"NG-KD-0014"}' >/dev/null
curl -sf -X POST "$PORTAL/subscriptions" -H 'Content-Type: application/json' \
  -d '{"patient_id":"PT-SCH-2","phone":"08030002222","language":"en","facility_id":"NG-KD-0014"}' >/dev/null

curl -sf -X POST "$PORTAL/portal/appointments" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"PT-SCH-1\",\"phone\":\"08030001111\",\"slot_id\":\"$SLOT1\"}" > "$TMP/book1.json"
python3 - "$TMP/book1.json" <<'PY'
import json, sys
a = json.load(open(sys.argv[1]))
assert a["status"] == "booked", a
# the advisory coverage snapshot decorates the appointment — the
# member was enrolled in step 1.5, so it must say eligible
cov = a.get("coverage") or {}
assert cov.get("eligible") is True, f"coverage snapshot missing: {a.get('coverage')}"
print("   booked:", a["id"], "coverage:", a.get("coverage"))
PY

echo "== 3. no-double-book: the second patient gets a loud 409 =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$PORTAL/portal/appointments" \
  -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"PT-SCH-2\",\"phone\":\"08030002222\",\"slot_id\":\"$SLOT1\"}")
[ "$CODE" = "409" ] || { echo "expected 409, got $CODE"; exit 1; }
echo "   second booking refused: 409"

echo "== 4. waitlist promotion: cancel hands the slot to the queue =="
curl -sf -X POST "$SCHED/waitlist" -H 'Content-Type: application/json' \
  -d '{"patient_id":"PT-SCH-2","facility_id":"NG-KD-0014","service":"anc"}' >/dev/null
APT1=$(python3 -c "import json;print(json.load(open('$TMP/book1.json'))['id'])")
curl -sf -X POST "$SCHED/appointments/$APT1/cancel" -H 'Content-Type: application/json' -d '{}' \
  | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['cancelled']['status'] == 'cancelled', d
assert d['promoted'] and d['promoted']['patient_id'] == 'PT-SCH-2', d
print('   cancelled; promoted from waitlist:', d['promoted']['id'])"

echo "== 5. the auth gate: wrong phone is an indistinguishable 404 =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' \
  "$PORTAL/portal/appointments?patient_id=PT-SCH-1&phone=08099999999")
[ "$CODE" = "404" ] || { echo "expected 404, got $CODE"; exit 1; }
echo "   wrong phone → 404 (no enumeration)"

echo "== 6. my appointments through the portal =="
curl -sf "$PORTAL/portal/appointments?patient_id=PT-SCH-2&phone=08030002222" \
  | python3 -c "
import json,sys
d = json.load(sys.stdin)
appts = d.get('appointments', d if isinstance(d, list) else [])
assert any(a['patient_id']=='PT-SCH-2' and a['status']=='booked' for a in appts), d
print('   PT-SCH-2 sees their promoted booking')"

echo
echo "SCHEDULING SMOKE: PASS"
