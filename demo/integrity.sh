#!/usr/bin/env bash
# Anti-collusion integrity smoke (ADR-0018) — insider fraud made
# unprofitable and visible, end to end (in-memory, no Postgres):
#
#   claims orchestrator + adjudicator (linkage gate, decision store,
#     hash-chained audit, integrity scorecard)
#   auditlake (independent verifier for the Go-written chain)
#   stockcheck (claim-vs-dispense-vs-procurement reconciliation)
#
# Asserts: a ghost claim (no encounter) PENDs with a linkage reason;
# a claim linked to a matching encounter APPROVEs; the patient disputes
# the ghost claim and it lands in the audit chain; the chain verifies
# with auditlake and a tampered copy fails; stock reconciliation flags
# the claimed-but-never-dispensed drug; the facility scorecard shows
# the dispute and the linkage failure with the grade in the open.
#
# Run from the repo root: bash demo/integrity.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/claims-orchestrator && go build -o "$TMP/claims" . )

MEMBERS="$TMP/members.json"
AUDIT="$TMP/audit.jsonl"
ENCOUNTERS="$TMP/encounters.jsonl"
echo '{}' > "$MEMBERS"
# The platform's encounter evidence: ENC-DEMO-1 happened (biometric
# presence), nothing else did.
cat > "$ENCOUNTERS" <<'EOF'
{"encounter_id":"ENC-DEMO-1","patient_id":"MBR-100","facility_id":"PHC-DEMO","date":"2026-10-18","presence":"biometric"}
EOF

LISTEN=:18080 MEMBERS_FILE="$MEMBERS" AUDIT_FILE="$AUDIT" \
  ADJUDICATOR="$PWD/packages/adjudicator/adjudicate.py" \
  ENCOUNTER_FILE="$ENCOUNTERS" "$TMP/claims" >"$TMP/claims.log" 2>&1 &
sleep 1.5
curl -sf -m 3 "http://127.0.0.1:18080/healthz" >/dev/null || {
  echo "claims service failed to start:"; cat "$TMP/claims.log"; exit 1; }
CLAIMS=http://127.0.0.1:18080

echo "== 1. enrol a member (actor named — anonymous enrolment is refused) =="
SCHEME=$(python3 -c "
import sys; sys.path.insert(0, 'packages/adjudicator')
from payers import DEFAULT_PAYERS
print(sorted(sch for p in DEFAULT_PAYERS for sch in p.schemes)[0])")
VALID=$(python3 -c "import datetime;print((datetime.date.today()+datetime.timedelta(days=365)).isoformat())")
curl -sf -X POST "$CLAIMS/enrolments" -H 'Content-Type: application/json' -d "{
  \"actor\":\"enrolment-officer-7\",\"member_id\":\"MBR-100\",\"name\":\"Demo Patient\",
  \"scheme\":\"$SCHEME\",\"valid_until\":\"$VALID\"}" >/dev/null
echo "   enrolled MBR-100 under $SCHEME"

echo "== 2. insider enrolment without an actor is refused =="
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$CLAIMS/enrolments" \
  -H 'Content-Type: application/json' \
  -d "{\"member_id\":\"GHOST-1\",\"name\":\"Ghost\",\"scheme\":\"$SCHEME\",\"valid_until\":\"$VALID\"}")
[ "$CODE" = "400" ] || { echo "expected 400 for actor-less enrolment, got $CODE"; exit 1; }
echo "   actor-less enrolment refused (400) — phantom patients start here"

echo "== 3. ghost claim: no encounter evidence → PEND, never silently paid =="
curl -sf -X POST "$CLAIMS/claims" -H 'Content-Type: application/json' -d '{
  "claim_id":"CLM-GHOST","member_id":"MBR-100","provider_id":"PHC-DEMO",
  "service_date":"2026-10-18","claimed_ngn":4500,
  "items":[{"code":"MAL-RDT","qty":1}]}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
dec = d
assert dec['decision'] == 'PEND', dec
assert dec['linkage']['linked'] is False, dec
assert any('LINKAGE' in f for f in dec['flags']), dec
print(f\"   ghost claim PENDed: {dec['flags'][0][:72]}\")"

echo "== 4. linked claim: matching encounter, biometric presence → APPROVE =="
curl -sf -X POST "$CLAIMS/claims" -H 'Content-Type: application/json' -d '{
  "claim_id":"CLM-REAL","member_id":"MBR-100","provider_id":"PHC-DEMO",
  "service_date":"2026-10-18","claimed_ngn":1500,"encounter_id":"ENC-DEMO-1",
  "items":[{"code":"MAL-RDT","qty":1}]}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
dec = d
assert dec['decision'] == 'APPROVE', dec
assert dec['linkage']['linked'] is True, dec
assert dec['linkage']['presence'] == 'biometric', dec
print(f\"   linked claim approved: payable NGN {dec['payable_ngn']} (presence=biometric)\")"

echo "== 5. the patient audits: every claim in their name is listable =="
curl -sf "$CLAIMS/claims/for-member?member_id=MBR-100" | python3 -c "
import json,sys
d = json.load(sys.stdin)
ids = sorted(c['claim']['claim_id'] for c in d['claims'])
assert ids == ['CLM-GHOST', 'CLM-REAL'], ids
print(f'   patient sees {len(ids)} claims in their name: {\", \".join(ids)}')"

echo "== 6. patient disputes the ghost claim — attributed, audited =="
curl -sf -X POST "$CLAIMS/claims/CLM-GHOST/dispute" -H 'Content-Type: application/json' \
  -d '{"actor":"MBR-100","reason":"I was never at PHC-DEMO on 2026-10-18"}' | python3 -c "
import json,sys
d = json.load(sys.stdin)
assert d['disputed'] is True, d
print('   dispute recorded:', d['claim_id'])"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$CLAIMS/claims/CLM-GHOST/dispute" \
  -H 'Content-Type: application/json' -d '{"actor":"","reason":""}')
[ "$CODE" = "400" ] || { echo "expected 400 for anonymous dispute, got $CODE"; exit 1; }
echo "   anonymous dispute refused (400) — an unnamed accusation is not evidence"

echo "== 7. the audit chain verifies — and tampering is detected =="
python3 packages/auditlake/auditlake.py verify "$AUDIT"
python3 - "$AUDIT" <<'PYEOF'
import subprocess, sys
raw = open(sys.argv[1]).read()
tampered = raw.replace("claim_disputed", "claim_disputed!", 1)
open("/tmp/audit-tampered.jsonl", "w").write(tampered)
r = subprocess.run(["python3", "packages/auditlake/auditlake.py", "verify",
                    "/tmp/audit-tampered.jsonl"], capture_output=True, text=True)
assert r.returncode != 0, "tampered chain must fail verification"
print("   tampered copy rejected — the chain is evidence, not a log")
PYEOF

echo "== 8. stock reconciliation: claimed-as-dispensed that never moved =="
cat > "$TMP/claim-lines.jsonl" <<'EOF'
{"facility_id":"PHC-DEMO","item_code":"ACT","quantity":500}
EOF
cat > "$TMP/dispensing.jsonl" <<'EOF'
{"facility_id":"PHC-DEMO","drug_code":"ACT","quantity":40,"status":"dispensed"}
EOF
cat > "$TMP/procurement.jsonl" <<'EOF'
{"facility_id":"PHC-DEMO","drug_code":"ACT","quantity":600}
EOF
python3 packages/stockcheck/stockcheck.py reconcile \
  --claims "$TMP/claim-lines.jsonl" --dispensing "$TMP/dispensing.jsonl" \
  --procurement "$TMP/procurement.jsonl" | python3 -c "
import json,sys
r = json.load(sys.stdin)
assert r['review'] == 1, r
drug = r['drugs'][0]
assert drug['verdict'] == 'REVIEW', drug
assert any('GHOST_DISPENSE' in f for f in drug['flags']), drug
print(f\"   REVIEW: {drug['flags'][0]}\")"

echo "== 9. facility scorecard: the collusion is visible in aggregate =="
curl -sf "$CLAIMS/integrity/PHC-DEMO" | python3 -c "
import json,sys
r = json.load(sys.stdin)
assert r['claims'] == 2, r
assert r['disputed'] == 1, r
assert r['linkage_failures'] == 1, r
assert r['grade'] == 'C', r   # 100 − 25 (dispute) − 10 (linkage) = 65
print(f\"   PHC-DEMO: {r['claims']} claims, {r['disputed']} disputed, \"
      f\"{r['linkage_failures']} linkage failure → grade {r['grade']}\")"

echo
echo "INTEGRITY SMOKE PASS — ghost claims pend, patients audit, chains verify, stock reconciles."
