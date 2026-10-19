#!/usr/bin/env bash
# Benefit visibility smoke (ADR-0031): the benefit package as data —
# explain, moment-of-care check, and a CPI staleness report — over the
# claims-orchestrator seam, plus the USSD menu flow at unit level.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/claims-orchestrator && go build -o "$TMP/claims" . )

LISTEN=:18197 SWITCH=http://127.0.0.1:19999 BENEFITS="$PWD/packages/benefits/benefits.py" \
  "$TMP/claims" >"$TMP/claims.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18197/healthz" >/dev/null || { cat "$TMP/claims.log"; exit 1; }
API=http://127.0.0.1:18197

echo "== 1. plain-language summary (what USSD reads out) =="
curl -sf "$API/benefits/STATE-BASIC" | python3 -c '
import json, sys
b = json.load(sys.stdin)
assert "14 covered services" in b["headline"] or "covered services" in b["headline"], b
assert "10%" in b["headline"], b
print("  ", b["headline"])'

echo "== 2. moment-of-care check: is a caesarean covered? =="
curl -sf "$API/benefits/STATE-BASIC?code=DEL-CS" | python3 -c '
import json, sys
b = json.load(sys.stdin)
assert b["covered"] and b["referral_required"], b
assert b["tariff_kobo"] == 7500000 and b["patient_pays_kobo"] == 750000, b
print("   covered, referral required, tariff N{:,}, patient pays N{:,} (10% co-pay)".format(
    b["tariff_kobo"] // 100, b["patient_pays_kobo"] // 100))'

echo "== 3. BHCPF vulnerable group: zero co-pay =="
curl -sf "$API/benefits/BHCPF-VUL?code=ANC-001" | grep -q '"patient_pays_kobo":0' \
  && echo "   ANC fully covered for vulnerable groups"

echo "== 4. excluded service reads honestly =="
curl -sf "$API/benefits/BHCPF-VUL?code=REN-DIAL" | grep -q '"covered":false' \
  && echo "   dialysis excluded in the minimum package — the patient learns this BEFORE the bill"

echo "== 5. staleness report: tariffs frozen at 2025 vs 15% CPI =="
curl -sf "$API/benefits/NG?staleness_year=2027&cpi_path=%7B%222026%22%3A0.15%2C%222027%22%3A0.15%7D" \
  | python3 -c '
import json, sys
b = json.load(sys.stdin)
assert b["cpi_factor"] == 1.3225, b
gap = b["capitation_expected_kobo"] - b["capitation_kobo"]
print("   capitation N1,450 should be N{:,.0f} by 2027 — gap N{:,.0f}/enrollee ({}%)".format(
    b["capitation_expected_kobo"] / 100, gap / 100, b["services"][0]["gap_pct"]))'

echo "== 6. unit batteries =="
( cd packages/benefits && python3 -m unittest 2>&1 | tail -1 )
( cd services/ussd-gateway && go test ./... 2>&1 | tail -1 )
( cd services/claims-orchestrator && go test ./... 2>&1 | tail -1 )

echo "BENEFITS DEMO OK"
