#!/usr/bin/env bash
# Citizen health wallet smoke (ADR-0030): government subsidy → restricted
# spend → refusal of non-health destination → diaspora gift → gift spend
# → expiry sweep — all on the real Rust ledger, auditable in the journal.
set -euo pipefail
cd "$(dirname "$0")/.."

TMP=$(mktemp -d)
cleanup() { kill $(jobs -p) 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT

echo "== build =="
( cd services/ledger && cargo build --offline --release 2>/dev/null || cargo build --release )
( cd services/wallet-service && go build -o "$TMP/wallet" . )
LEDGER=""
for cand in services/ledger/target/release/oh_ledger "${CARGO_TARGET_DIR:-/nonexistent}/release/oh_ledger"; do
  [ -x "$cand" ] && LEDGER="$cand" && break
done
[ -n "$LEDGER" ] || { echo "ledger binary not found"; exit 1; }

LISTEN=:18199 LEDGER="$(readlink -f "$LEDGER")" LEDGER_DATA_DIR="$TMP/ledger" "$TMP/wallet" >"$TMP/wallet.log" 2>&1 &
sleep 1.2
curl -sf -m 3 "http://127.0.0.1:18199/healthz" >/dev/null || { cat "$TMP/wallet.log"; exit 1; }
API=http://127.0.0.1:18199

echo "== 0. seed treasury + PSP floats =="
echo '{"op":"open","id":"TREASURY-FLOAT","opening":100000000}
{"op":"open","id":"PSP-FLOAT","opening":100000000}' | LEDGER_DATA_DIR="$TMP/ledger" "$LEDGER" >/dev/null

echo "== 1. government funds a citizen wallet (BHCPF, ₦5,000) =="
curl -sf -X POST "$API/citizens/fund" -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"GENERAL","amount_kobo":500000,"programme":"BHCPF","ref":"batch-001"}' >/dev/null
curl -sf "$API/citizens/NIN-100/balance" | grep -q '"total_kobo":500000' \
  && echo "   wallet funded; allow-list visible in balance response"

echo "== 2. citizen pays a facility =="
curl -sf -X POST "$API/citizens/pay" -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"GENERAL","destination":"WALLET-phc-karu","amount_kobo":150000,"ref":"pay-1"}' >/dev/null \
  && echo "   facility payment posted (₦1,500)"

echo "== 3. airtime purchase is refused (403) =="
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/citizens/pay" \
  -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"GENERAL","destination":"AIRTIME-MTN","amount_kobo":10000,"ref":"pay-2"}')
[ "$code" = "403" ] && echo "   refused before the ledger — the money never moved"

echo "== 4. diaspora gift (private funder, PSP settlement) =="
curl -sf -X POST "$API/citizens/gift" -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"MATERNAL","amount_kobo":1000000,"giver":"aunt-london","ref":"remit-991","expires_in_days":180}' >/dev/null
grep -q "GIFT-aunt-london-remit-991" "$TMP/ledger/journal.jsonl" \
  && echo "   gift attributed in the ledger journal"
echo "   maternal envelope keeps its narrower policy:"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/citizens/pay" \
  -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"MATERNAL","destination":"PHARM-medplus","amount_kobo":5000,"ref":"pay-3"}')
[ "$code" = "403" ] && echo "   maternal gift → pharmacy refused (facility-only policy)"
curl -sf -X POST "$API/citizens/pay" -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"MATERNAL","destination":"WALLET-fmc-jabi","amount_kobo":400000,"ref":"pay-4"}' >/dev/null \
  && echo "   maternal gift → facility allowed"

echo "== 5. balances reflect everything =="
curl -sf "$API/citizens/NIN-100/balance" | python3 -c '
import json, sys
b = json.load(sys.stdin)
assert b["total_kobo"] == 500000 - 150000 + 1000000 - 400000, b
assert b["envelopes"]["GENERAL"] == 350000 and b["envelopes"]["MATERNAL"] == 600000, b
print("   general ₦3,500 + maternal ₦6,000 = total ₦9,500")'

echo "== 6. exactly-once: replayed gift does not double-fund =="
curl -sf -X POST "$API/citizens/gift" -H 'Content-Type: application/json' \
  -d '{"nin":"NIN-100","envelope":"MATERNAL","amount_kobo":1000000,"giver":"aunt-london","ref":"remit-991","expires_in_days":180}' \
  | grep -q '"duplicate":true' && echo "   replay answered as duplicate"

echo "== 7. unit batteries =="
( cd services/wallet-service && go test ./... 2>&1 | tail -1 )
( cd services/ussd-gateway && go test ./... 2>&1 | tail -1 )

echo "WALLETS DEMO OK"
