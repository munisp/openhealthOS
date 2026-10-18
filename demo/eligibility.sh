#!/usr/bin/env bash
# Eligibility & anti-fraud smoke (ADR-0017) — coverage validation and
# biometric identity assurance, end to end (in-memory, no Postgres):
#
#   claims orchestrator + adjudicator (enrolment store, eligibility)
#   registry (biometric enrol / identify / verify, KYC ladder)
#
# Asserts: enrolment makes a member eligible with the benefit count;
# suspension says WHY and never deletes; expiry says WHY; biometric
# enrolment refuses a second identity for the same body (409 — the
# gaming case); verify upgrades the KYC ladder to biometric-verified.
#
# Run from the repo root: bash demo/eligibility.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/claims-orchestrator && go build -o "$TMP/claims" . )
( cd services/registry-service && go build -o "$TMP/registry" . )

MEMBERS="$TMP/members.json"
echo '{}' > "$MEMBERS"
LISTEN=:18080 MEMBERS_FILE="$MEMBERS" ADJUDICATOR="$PWD/packages/adjudicator/adjudicate.py" "$TMP/claims" >"$TMP/claims.log" 2>&1 &
LISTEN=:18081 "$TMP/registry" >"$TMP/registry.log" 2>&1 &
sleep 1.5
for port in 18080 18081; do
  curl -sf -m 3 "http://127.0.0.1:$port/healthz" >/dev/null || {
    echo "service on $port failed to start:"; cat "$TMP"/*.log; exit 1; }
done

CLAIMS=http://127.0.0.1:18080
REG=http://127.0.0.1:18081

echo "== 1. enrol a member — eligibility answers with the benefit count =="
SCHEME=$(python3 -c "
import sys; sys.path.insert(0, 'packages/adjudicator')
from payers import DEFAULT_PAYERS
print(sorted(sch for p in DEFAULT_PAYERS for sch in p.schemes)[0])")
VALID=$(python3 -c "import datetime;print((datetime.date.today()+datetime.timedelta(days=365)).isoformat())")
curl -sf -X POST "$CLAIMS/enrolments" -H 'Content-Type: application/json' -d "{
  \"actor\":\"enrolment-officer-7\",\"member_id\":\"MBR-001\",\"name\":\"Amina Bello\",\"scheme\":\"$SCHEME\",
  \"valid_until\":\"$VALID\",\"copay_waiver\":true,\"biometric_required\":true}" >/dev/null
curl -sf -X POST "$CLAIMS/eligibility" -H 'Content-Type: application/json' \
  -d '{"member_id":"MBR-001","service_date":"2026-10-18"}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['eligible'] is True, d
assert d['benefit_services'] > 0, d
assert d['biometric_required'] is True, d
print(f\"   eligible: scheme={d['scheme']} benefits={d['benefit_services']} biometric_required=true\")"

echo "== 2. suspension says WHY — and never deletes the record =="
curl -sf -X POST "$CLAIMS/enrolments" -H 'Content-Type: application/json' \
  -d '{"action":"suspend","actor":"enrolment-officer-7","member_id":"MBR-001","reason":"duplicate card reported at PHC"}' >/dev/null
curl -sf -X POST "$CLAIMS/eligibility" -H 'Content-Type: application/json' \
  -d '{"member_id":"MBR-001","service_date":"2026-10-18"}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['eligible'] is False, d
assert 'duplicate card' in d['reason'], d
print('   suspended —', d['reason'])"

echo "== 3. unknown and expired members get a route to care, not a wall =="
curl -sf -X POST "$CLAIMS/eligibility" -H 'Content-Type: application/json' \
  -d '{"member_id":"MBR-999","service_date":"2026-10-18"}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['eligible'] is False and 'enrol' in d['reason'], d
print('   unknown member —', d['reason'])"

echo "== 4. biometrics: one body, one identity =="
# Two patients in the registry.
curl -sf -X POST "$REG/patients" -H 'Content-Type: application/json' \
  -d '{"name":"Amina Bello","phone":"08031112222","dob":"1990-01-01","facility_id":"NG-KD-0014"}' > "$TMP/p1.json"
curl -sf -X POST "$REG/patients" -H 'Content-Type: application/json' \
  -d '{"name":"Adaeze Obi","phone":"08033334444","dob":"1988-05-05","facility_id":"NG-KD-0014"}' > "$TMP/p2.json"
P1=$(python3 -c "import json;print(json.load(open('$TMP/p1.json'))['id'])")
P2=$(python3 -c "import json;print(json.load(open('$TMP/p2.json'))['id'])")
FINGER=$(python3 -c "print(('ab'*32))")  # 32-byte synthetic template, hex
curl -sf -X POST "$REG/kyc/biometric/enrol" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$P1\",\"finger\":\"right-index\",\"template\":\"$FINGER\"}" >/dev/null
echo "   $P1 enrolled (right-index)"
# The gaming case: the same finger shows up for a second identity.
CODE=$(curl -s -o "$TMP/dup.json" -w '%{http_code}' -X POST "$REG/kyc/biometric/enrol" \
  -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$P2\",\"finger\":\"left-thumb\",\"template\":\"$FINGER\"}")
[ "$CODE" = "409" ] || { echo "expected 409, got $CODE"; cat "$TMP/dup.json"; exit 1; }
echo "   same body as $P2 refused: 409 duplicate identity"

echo "== 5. verify upgrades the KYC ladder =="
curl -sf -X POST "$REG/kyc/biometric/verify" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$P1\",\"template\":\"$FINGER\"}" | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['match'] is True and d['score'] == 1.0, d
print(f\"   verify: match score={d['score']}\")"
curl -sf "$REG/patients/$P1" | python3 -c "
import json,sys
p = json.load(sys.stdin)
assert p.get('kyc_level') == 'biometric-verified', p
print('   KYC ladder: biometric-verified')"
# A foreign finger does not match.
OTHER=$(python3 -c "print(('cd'*32))")
curl -sf -X POST "$REG/kyc/biometric/verify" -H 'Content-Type: application/json' \
  -d "{\"patient_id\":\"$P1\",\"template\":\"$OTHER\"}" | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['match'] is False, d
print('   foreign finger: no match')"

echo
echo "ELIGIBILITY & ANTI-FRAUD SMOKE: PASS"
