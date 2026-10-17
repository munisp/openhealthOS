#!/usr/bin/env bash
# Clinical loop smoke — the whole care journey across services, in-memory
# (no Postgres needed). Exercises ADR-0010's wiring end to end:
#
#   telehealth consult ──close──▶ SHR: FHIR Encounter
#   telehealth prescribe ────────▶ SHR: FHIR MedicationRequest
#                        └──route─▶ pharmacy dispense-request (allergy gate)
#   pharmacy dispense ───────────▶ stock decrement + claim draft (atomic)
#
# Run from the repo root: bash demo/clinical-loop.sh
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() {
  kill $(jobs -p) 2>/dev/null || true
  # binaries linger if the shell is killed hard — match by build path
  pkill -f "$TMP/(shr|pharmacy|telehealth)" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT

echo "== build =="
( cd services/shr-service && go build -o "$TMP/shr" . )
( cd services/pharmacy-service && go build -o "$TMP/pharmacy" . )
( cd services/telehealth-service && go build -o "$TMP/telehealth" . )

LISTEN=:18085 "$TMP/shr" >"$TMP/shr.log" 2>&1 &
LISTEN=:18088 "$TMP/pharmacy" >"$TMP/pharmacy.log" 2>&1 &
LISTEN=:18087 SHR_URL=http://127.0.0.1:18085 PHARMACY_URL=http://127.0.0.1:18088 "$TMP/telehealth" >"$TMP/telehealth.log" 2>&1 &
sleep 1.5
for port in 18085 18088 18087; do
  curl -sf -m 3 "http://127.0.0.1:$port/healthz" >/dev/null || {
    echo "service on $port failed to start:"; cat "$TMP"/*.log; exit 1; }
done

J='Content-Type: application/json'
FAC="PHC-$RANDOM$RANDOM"   # hermetic per run — services are in-memory
say() { echo; echo "── $*"; }

say "1. pharmacy receives stock: AL8020 x100 at $FAC"
curl -sf -X POST http://127.0.0.1:18088/stock/receive -H "$J" \
  -d "{\"facility_id\":\"$FAC\",\"drug_code\":\"AL8020\",\"quantity\":100,\"reorder_level\":20}" >/dev/null && echo "   stocked"

say "2. telehealth consult opens for PT-1"
CONSULT=$(curl -sf -X POST http://127.0.0.1:18087/consults -H "$J" \
  -d "{\"patient_id\":\"PT-1\",\"facility_id\":\"$FAC\",\"nurse_id\":\"N-1\",\"complaint\":\"fever x 2 days\"}")
CID=$(echo "$CONSULT" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
echo "   consult $CID"
curl -sf -X POST http://127.0.0.1:18087/consults/$CID/assess -H "$J" \
  -d '{"protocol":"IMCI fever","assessment":"RDT positive, no danger signs"}' >/dev/null
echo "   assessed (prescribable)"

say "3. prescribe AL8020 x24 (no allergy) — routes to SHR + pharmacy"
RX=$(curl -sf -X POST http://127.0.0.1:18087/prescriptions -H "$J" \
  -d "{\"consult_id\":\"$CID\",\"prescriber\":\"nurse-N-1\",\"medication\":\"AL8020\",\"dose\":\"80/20 bd x 3d\",\"quantity\":24,\"patient_id\":\"PT-1\",\"facility_id\":\"$FAC\",\"allergies\":[],\"pharmacy_id\":\"$FAC\"}")
echo "$RX" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   pharmacy_routed:", d["pharmacy_routed"], "| rx:", d["prescription"]["id"])'
RXID=$(echo "$RX" | python3 -c 'import json,sys; print(json.load(sys.stdin)["prescription"]["id"])')

say "4. SHR holds the FHIR MedicationRequest (provenance: $FAC)"
curl -sf http://127.0.0.1:18085/fhir/MedicationRequest/$RXID -H "X-Facility-ID: $FAC" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   status:", d.get("status"), "| intent:", d.get("intent"), "| qty:", d["dispenseRequest"]["quantity"]["value"])'

say "5. close consult — SHR gets the FHIR Encounter"
curl -sf -X POST http://127.0.0.1:18087/consults/$CID/close -H "$J" -d '{"outcome":"routine-closure"}' >/dev/null
RESP=$(curl -s -w "\n%{http_code}" http://127.0.0.1:18085/fhir/Encounter/$CID -H "X-Facility-ID: $FAC")
CODE=$(echo "$RESP" | tail -1)
if [ "$CODE" != "200" ]; then echo "   encounter GET failed [$CODE]: $(echo "$RESP" | head -1)"; exit 1; fi
echo "$RESP" | head -1 \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   encounter status:", d.get("status"), "| class:", (d.get("class") or {}).get("code"))'

say "6. pharmacy counter: pending request -> dispense -> claim draft"
REQID=$(curl -sf "http://127.0.0.1:18088/dispense-requests?facility=$FAC&status=pending" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d[0]["id"])')
DISP=$(curl -sf -X POST http://127.0.0.1:18088/dispense-requests/$REQID/dispense -H "$J" -d '{"staff_id":"pharm-1"}')
echo "$DISP" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   dispensed:", d["request"]["status"], "| claim draft ₦", d["claim_draft"]["total_ngn"])'

say "7. stock decremented atomically (100 -> 76)"
curl -sf "http://127.0.0.1:18088/stock?facility=$FAC" \
  | python3 -c 'import json,sys; s=json.load(sys.stdin); print("   AL8020:", [x["quantity"] for x in s if x["drug_code"]=="AL8020"][0])'

say "8. allergy gate: penicillin-allergic patient prescribed AMOX500"
C2=$(curl -sf -X POST http://127.0.0.1:18087/consults -H "$J" \
  -d "{\"patient_id\":\"PT-2\",\"facility_id\":\"$FAC\",\"nurse_id\":\"N-1\",\"complaint\":\"cough\"}")
CID2=$(echo "$C2" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')
curl -sf -X POST http://127.0.0.1:18087/consults/$CID2/assess -H "$J" \
  -d '{"protocol":"IMCI cough","assessment":"fast breathing"}' >/dev/null
curl -sf -X POST http://127.0.0.1:18087/prescriptions -H "$J" \
  -d "{\"consult_id\":\"$CID2\",\"prescriber\":\"nurse-N-1\",\"medication\":\"AMOX500\",\"dose\":\"500mg tds x 7d\",\"quantity\":21,\"patient_id\":\"PT-2\",\"facility_id\":\"$FAC\",\"allergies\":[\"penicillin\"],\"pharmacy_id\":\"$FAC\"}" >/dev/null
curl -sf "http://127.0.0.1:18088/dispense-requests?facility=$FAC&status=rejected" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("   rejected:", d[0]["drug_code"], "|", d[0]["reject_reason"])'

echo; echo "CLINICAL LOOP OK"
