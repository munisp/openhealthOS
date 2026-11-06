#!/usr/bin/env bash
# VHC ministry demo — one command: seed the pilot fortnight, build the
# walkthrough, render the run-of-show. Exit 1 (and print STOP THE DEMO)
# if any red finding is present: you do not present past a red finding.
set -euo pipefail
cd "$(dirname "$0")/.."

PKGS=packages
export PYTHONPATH="${PKGS}/travelpass"
for p in crosslink drugkb surveillance poe_screen steward medrec inpatient \
         terminology mammoprog offlinespec teachfile formulary earlywarn \
         cervixscreen usguard callrecall sepsis teleicu acuity telepath \
         teleread reportkit pacs fertility rota triggers vitals mdt; do
  PYTHONPATH="${PYTHONPATH}:${PKGS}/${p}"
done

SEED="${VHC_DEMO_SEED:-42}"
DAYS="${VHC_DEMO_DAYS:-14}"
CURRENCY="${VHC_DEMO_CURRENCY:-€}"
OUT="${VHC_DEMO_OUT:-docs/demo/ministry-walkthrough.md}"

python3 -m vhdemo --seed "$SEED" --days "$DAYS" \
  --currency "$CURRENCY" --out "$OUT" "$@"

echo "== walkthrough complete =="
