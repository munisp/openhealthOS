#!/usr/bin/env bash
# KPI plane smoke (ADR-0033): the nightly state-plane job builds one
# kpi_plane.json from lakehouse gold + the ML registry; the api-gateway
# serves it at /api/kpi/plane; the dashboards' economics/ML-health
# boards read exactly that document.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== 0. fixtures =="
mkdir -p "$TMP/lakehouse/gold" "$TMP/registry/fraud-mlp/v1"
cat > "$TMP/facilities.json" <<'JSON'
[{"id":"PHC-1","name":"Kawo PHC","latitude":10.599,"longitude":7.434},
 {"id":"PHC-2","name":"Umuahia PHC","latitude":5.532,"longitude":7.486}]
JSON
cat > "$TMP/members.jsonl" <<'JSON'
{"id":"M1","latitude":10.61,"longitude":7.45}
{"id":"M2","latitude":10.70,"longitude":7.50}
{"id":"M3","latitude":5.54,"longitude":7.49}
JSON
cat > "$TMP/enrollees.jsonl" <<'JSON'
{"id":"E1","age":2}
{"id":"E2","age":34}
{"id":"E3","age":55,"chronic":true}
JSON
cat > "$TMP/encounters.jsonl" <<'JSON'
{"code":"CONS-GEN","enrollee_id":"E2","staff":[{"minutes":15,"kobo_per_minute":100}],"consumables":[{"kobo":5000}]}
{"code":"LAB-FBC","enrollee_id":"E3","staff":[{"minutes":15,"kobo_per_minute":100}],"consumables":[{"kobo":90000}]}
{"code":"CONS-GEN","staff":[{"minutes":15,"kobo_per_minute":100}],"consumables":[]}
JSON
cat > "$TMP/claims.jsonl" <<'JSON'
{"hmo":"NHIA","status":"paid","amount_kobo":500000,"amount_ngn":5000,"tariff_ngn":5000,"qty":1,"hour":10,"day":2,"facility_id":"PHC-1","member_id":"E2","code":"CONS-GEN"}
{"hmo":"NHIA","status":"denied","denial_reason":"missing-referral","amount_kobo":7500000,"amount_ngn":75000,"tariff_ngn":75000,"qty":1,"hour":11,"day":2,"facility_id":"PHC-1","member_id":"E3","code":"DEL-CS"}
JSON
cp "$TMP/claims.jsonl" "$TMP/claims_ref.jsonl"
echo v1 > "$TMP/registry/fraud-mlp/champion"
echo '{"auc":0.91,"precision_at_k":0.44}' > "$TMP/registry/fraud-mlp/v1/metrics.json"
echo '{"data_hash":"demo"}' > "$TMP/registry/fraud-mlp/v1/card.json"
echo '{"events_by_type":{"encounter":3}}' > "$TMP/lakehouse/gold/aggregates.json"

echo "== 1. nightly build =="
python3 packages/kpiplane/kpiplane.py \
  --lakehouse "$TMP/lakehouse" \
  --facilities "$TMP/facilities.json" \
  --members "$TMP/members.jsonl" \
  --enrollees "$TMP/enrollees.jsonl" \
  --encounters "$TMP/encounters.jsonl" \
  --claims "$TMP/claims.jsonl" \
  --claims-reference "$TMP/claims_ref.jsonl" \
  --registry "$TMP/registry" | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("   sections ok:", ", ".join(r["sections_ok"]))
assert set(r["sections_ok"]) == {"facility_economics", "panel_risk", "catchments", "ml_health"}'
python3 -c '
import json
p = json.load(open("'$TMP'/lakehouse/gold/kpi_plane.json"))
assert p["sections"]["facility_economics"]["panel"]["panel_size"] == 3
assert p["sections"]["catchments"]["facilities"][0]["assigned"] == 2
assert p["sections"]["ml_health"]["models"][0]["champion"] == "v1"
assert p["sections"]["ml_health"]["drift"]["verdict"] in ("stable","moderate","significant")
print("   plane verified: panel=3, catchments assigned, champion v1, drift computed")'

echo "== 2. gateway serves the plane =="
( cd services/api-gateway && go build -o "$TMP/gw" . )
LISTEN=:18211 KPI_PLANE_PATH="$TMP/lakehouse/gold/kpi_plane.json" \
  OIDC_DISABLED=1 "$TMP/gw" >"$TMP/gw.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18211/api/kpi/plane" | python3 -c '
import json, sys
p = json.load(sys.stdin)
sec = p["sections"]
worst = sec["facility_economics"]["margins"]["lines"][0]
print("   /api/kpi/plane → margins lines=%d, worst=%s ₦%.0f" % (
    len(sec["facility_economics"]["margins"]["lines"]),
    worst["code"], worst["margin_kobo"]/100))
print("   drift verdict:", sec["ml_health"]["drift"]["verdict"])
assert sec["panel_risk"]["status"] == "ok"'
code=$(curl -s -o /dev/null -w '%{http_code}' -m 3 -X POST "http://127.0.0.1:18211/api/kpi/plane")
[ "$code" = 405 ] && echo "   POST /api/kpi/plane → 405 (read-only, as designed)"

echo "== 3. dashboards consume it (pure transforms) =="
( cd apps/dashboards && node --test tests/kpi.test.mjs 2>&1 | grep -E "^# (pass|fail)" )

echo "== 4. unit batteries =="
( cd packages/kpiplane && python3 -m unittest 2>&1 | tail -1 )
( cd services/api-gateway && go test ./... 2>&1 | tail -1 )

echo "KPI PLANE DEMO OK"
