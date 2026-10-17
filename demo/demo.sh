#!/usr/bin/env bash
# End-to-end claims demo: adjudication → ledger → mock settlement switch.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== building =="
cargo build --release --quiet --manifest-path services/ledger/Cargo.toml
(cd services/payment-switch-mock && go build -o payment-switch-mock .)
(cd services/claims-orchestrator && go build -o claims-orchestrator .)

pkill -f payment-switch-mock 2>/dev/null || true
pkill -f claims-orchestrator 2>/dev/null || true
sleep 0.3

./services/payment-switch-mock/payment-switch-mock & MS=$!
sleep 0.4
SWITCH=http://127.0.0.1:9090 \
ADJUDICATOR=packages/adjudicator/adjudicate.py \
LEDGER=services/ledger/target/release/oh_ledger \
  ./services/claims-orchestrator/claims-orchestrator & OR=$!
sleep 0.4

echo "== APPROVED CLAIM (C-1001) =="
curl -s localhost:8080/claims -d @examples/claims/approve.json | python3 -m json.tool
echo "== PENDED CLAIM (C-1002, price drift) =="
curl -s localhost:8080/claims -d @examples/claims/pend.json | python3 -m json.tool

kill $MS $OR 2>/dev/null || true
echo "== demo complete =="
