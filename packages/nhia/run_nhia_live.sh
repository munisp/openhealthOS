#!/usr/bin/env bash
# run_nhia_live.sh — submit a staged NHIA envelope against the REAL scheme
# gateway. Honest path: without credentials this exits 2 and says why.
#
# Usage:
#   NHIA_BASE_URL=https://gateway.nhia.gov.ng NHIA_TOKEN=... \
#     bash run_nhia_live.sh envelope.json /var/openhealth/nhia-outbox
set -euo pipefail

cd "$(dirname "$0")"

if [[ -z "${NHIA_BASE_URL:-}" || -z "${NHIA_TOKEN:-}" ]]; then
  echo "LIVE SUBMISSION UNAVAILABLE: set NHIA_BASE_URL and NHIA_TOKEN" >&2
  echo "(scheme gateway credentials are issued per facility by the scheme;" >&2
  echo " the sandbox lifecycle — stage/submit/reconcile — is fully tested" >&2
  echo " in test_flow.py and needs no network.)" >&2
  exit 2
fi

ENVELOPE="${1:?usage: run_nhia_live.sh envelope.json outbox_dir}"
OUTBOX="${2:?usage: run_nhia_live.sh envelope.json outbox_dir}"

python3 - "$ENVELOPE" "$OUTBOX" <<'PY'
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath("flow.py")))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath("flow.py")),
                                "..", "adapters"))
from flow import SubmissionFlow
from nhia import NHIAAdapter

doc = json.load(open(sys.argv[1]))
adapter = NHIAAdapter(base_url=os.environ["NHIA_BASE_URL"],
                      token=os.environ["NHIA_TOKEN"], sandbox=False)
flow = SubmissionFlow(sys.argv[2], adapter)
led = flow.stage(doc["scheme"], doc["facility_id"], doc["period"],
                 doc["claims"])
led = flow.submit(led["submission_hash"])
print(json.dumps({"submission_hash": led["submission_hash"],
                  "state": led["state"],
                  "references": [c["reference"] for c in led["claims"]]},
                 indent=2))
PY
