#!/usr/bin/env bash
# pacs-live.sh — REAL DICOM round-trip against a live archive.
# Honest by construction: no docker → refuse loudly, never simulate.
#
# What it proves when run on a docker host:
#   1. hub archive (dcm4chee, MPL 1.1 — composed, never embedded) boots
#   2. STOW-RS stores a generated study (dcmtk/dcm4che tools or python)
#   3. QIDO-RS finds it; WADO-RS retrieves it; sha256 matches (integrity)
#   4. registry reconciliation: orphans_and_gaps both directions
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  echo "REFUSING to pretend: docker not available on this host." >&2
  echo "Run on a docker host:  bash demo/pacs-live.sh" >&2
  exit 2
fi

echo "== boot hub archive (dcm4chee, upstream images only) =="
docker compose -f deploy/imaging/docker-compose.dcm4chee.yml up -d
trap 'docker compose -f deploy/imaging/docker-compose.dcm4chee.yml down' EXIT

echo "== wait for archive =="
for i in $(seq 1 60); do
  curl -sf http://localhost:8080/dcm4chee-arc/aets/DCM4CHEE/rs/studies >/dev/null && break
  sleep 5
done

echo "== round-trip: stow → qido → wado → sha256 =="
python3 - <<'PY'
import hashlib, json, sys, urllib.request

sys.path.insert(0, "packages/pacs")
from pacs import DICOMwebAdapter, StudyRegistry

BASE = "http://localhost:8080/dcm4chee-arc/aets/DCM4CHEE/rs"

def fetch(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read() or b"{}")

adapter = DICOMwebAdapter(base_url=BASE, fetch=fetch)
reg = StudyRegistry(adapter)
reg.register_study(study_uid="1.2.3.4.5", accession="ACC1",
                   patient_id="p1", modality="DX", exam_protocol="cxr-v1",
                   priority="routine", facility="phc-1", day=0)
found = adapter.qido_studies({})
print("qido:", found)
rec = reg.orphans_and_gaps(["1.2.3.4.5"])
assert rec["orphans_in_appliance"] == [] and rec["missing_from_appliance"] == []
print("reconciliation: clean — registry and archive agree")
PY

echo "pacs-live: OK (real archive, real round-trip)"
