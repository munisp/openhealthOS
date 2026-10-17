#!/usr/bin/env bash
# End-to-end claims demo: adjudication → ledger → mock settlement switch.
#
# Binaries build into a scratch dir ($TMPDIR or /tmp) — development checkouts
# on mounts without exec permission still run the demo.
set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openhealth-demo.XXXXXX")"
trap 'rm -rf "$BUILD_DIR"' EXIT

echo "== building =="
CARGO_TARGET_DIR="$BUILD_DIR/cargo" cargo build --release --quiet --manifest-path services/ledger/Cargo.toml
(cd services/payment-switch-mock && go build -o "$BUILD_DIR/payment-switch-mock" .)
(cd services/claims-orchestrator && go build -o "$BUILD_DIR/claims-orchestrator" .)

pkill -f openhealth-demo 2>/dev/null || true
sleep 0.3

"$BUILD_DIR/payment-switch-mock" & MS=$!
sleep 0.4
PORT="${DEMO_PORT:-18080}"
LISTEN=":$PORT" SWITCH=http://127.0.0.1:9090 \
ADJUDICATOR=packages/adjudicator/adjudicate.py \
LEDGER="$BUILD_DIR/cargo/release/oh_ledger" \
  "$BUILD_DIR/claims-orchestrator" & OR=$!
sleep 0.4

echo "== APPROVED CLAIM (C-1001) =="
curl -s "localhost:${DEMO_PORT:-18080}/claims" -d @examples/claims/approve.json | python3 -m json.tool
echo "== PENDED CLAIM (C-1002, price drift) =="
curl -s "localhost:${DEMO_PORT:-18080}/claims" -d @examples/claims/pend.json | python3 -m json.tool

kill $MS $OR 2>/dev/null || true
echo "== demo complete =="
