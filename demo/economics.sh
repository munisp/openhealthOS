#!/usr/bin/env bash
# Provider economics smoke (ADR-0032): margin-per-code against the NHIA
# tariff schedule, capitation panel P&L per risk band, denial analytics,
# and a tariff simulation — through the claims-orchestrator seam.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/claims-orchestrator && go build -o "$TMP/claims" . )
LISTEN=:18193 SWITCH=http://127.0.0.1:19999 \
  BENEFITS="$PWD/packages/benefits/benefits.py" \
  ECONOMICS="$PWD/packages/economics/economics.py" \
  "$TMP/claims" >"$TMP/claims.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18193/healthz" >/dev/null || { cat "$TMP/claims.log"; exit 1; }
API=http://127.0.0.1:18193

echo "== 1. margin per service code (FFS) =="
curl -sf -X POST "$API/economics/margins" -H 'Content-Type: application/json' -d '{
  "overhead_kobo_per_day": 500000,
  "encounters": [
    {"code":"DEL-CS","staff":[{"minutes":180,"kobo_per_minute":10000}],"consumables":[{"kobo":2000000}],"los_days":2},
    {"code":"DEL-CS","staff":[{"minutes":200,"kobo_per_minute":10000}],"consumables":[{"kobo":2200000}],"los_days":3},
    {"code":"CONS-GEN","staff":[{"minutes":20,"kobo_per_minute":5000}],"consumables":[]},
    {"code":"REN-DIAL","staff":[{"minutes":240,"kobo_per_minute":12000}],"consumables":[{"kobo":4000000}],"los_days":0}
  ]}' | python3 -c '
import json, sys
r = json.load(sys.stdin)
for l in r["lines"]:
    print("   %-9s count=%d margin=₦%.0f (%s%%)" % (l["code"], l["count"], l["margin_kobo"]/100, l["margin_pct"]))
assert r["tariff_year"] == 2025'

echo "== 2. capitation panel P&L per risk band =="
python3 - "$API" <<'PY'
import json, sys, urllib.request
api = sys.argv[1]
enrollees = ([{"id": f"a{i}", "age": 30} for i in range(80)]
             + [{"id": f"u{i}", "age": 3} for i in range(15)]
             + [{"id": f"c{i}", "age": 55, "chronic": True} for i in range(5)])
encounters = ([{"code": "CONS-GEN", "enrollee_id": "a1",
                "staff": [{"minutes": 20, "kobo_per_minute": 5000}], "consumables": []}] * 40
              + [{"code": "LAB-FBC", "enrollee_id": "c0",
                  "staff": [{"minutes": 15, "kobo_per_minute": 5000}],
                  "consumables": [{"kobo": 90000}]}] * 12
              + [{"code": "CONS-GEN", "enrollee_id": "ghost",
                  "staff": [{"minutes": 20, "kobo_per_minute": 5000}], "consumables": []}] * 8)
req = urllib.request.Request(api + "/economics/panel",
    data=json.dumps({"enrollees": enrollees, "encounters": encounters,
                     "capitation_kobo": 145000}).encode(),
    headers={"Content-Type": "application/json"})
r = json.load(urllib.request.urlopen(req))
for b in r["bands"]:
    print("   %-10s n=%-4d margin=₦%.2f/enrollee" % (
        b["band"], b["enrollees"],
        (b["margin_per_enrollee_kobo"] or 0) / 100))
assert r["walk_in_cost_kobo"] > 0
print("   walk-in care (no capitation attribution): ₦%.0f" % (r["walk_in_cost_kobo"]/100))
PY

echo "== 3. denial analytics =="
curl -sf -X POST "$API/economics/denials" -H 'Content-Type: application/json' -d '{
  "claims": [
    {"hmo":"HMO-A","status":"paid","amount_kobo":500000},
    {"hmo":"HMO-A","status":"denied","denial_reason":"missing-referral","amount_kobo":7500000},
    {"hmo":"HMO-A","status":"denied","denial_reason":"documentation-gap","amount_kobo":3500000},
    {"hmo":"HMO-B","status":"denied","denial_reason":"not-covered","amount_kobo":8000000},
    {"hmo":"HMO-B","status":"paid","amount_kobo":2000000}
  ]}' | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("   denied %d of %d claims; preventable share %.1f%% (₦%.0f)" % (
    r["denied"], r["claims"], r["preventable_share_pct"], r["preventable_denied_kobo"]/100))
for h, a in r["by_hmo"].items():
    print("   %s denial rate %.1f%%" % (h, a["denial_rate_pct"]))
assert r["preventable_share_pct"] > 50'

echo "== 4. tariff simulation before publishing =="
curl -sf -X POST "$API/economics/tariff-sim" -H 'Content-Type: application/json' -d '{
  "utilization":[{"code":"DEL-CS","volume":120},{"code":"CONS-GEN","volume":9000}],
  "cpi_path":{"2026":0.15,"2027":0.15},"target_year":2027}' | python3 -c '
import json, sys
r = json.load(sys.stdin)
print("   CPI factor %.2f → pool outflow +%.1f%% (₦%.0f), co-pay +₦%.0f" % (
    r["cpi_factor"], r["pool_outflow_delta_pct"],
    r["pool_outflow_delta_kobo"]/100, r["enrollee_copay_delta_kobo"]/100))'

echo "== 5. unit batteries =="
( cd packages/economics && python3 -m unittest 2>&1 | tail -1 )
( cd services/claims-orchestrator && go test ./... 2>&1 | tail -1 )

echo "ECONOMICS DEMO OK"
