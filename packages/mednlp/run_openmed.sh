#!/bin/sh
# run_openmed.sh — live verification path for the OpenMed spike (ADR-0065).
# Installs the SDK + HF extras, downloads the pinned PII/NER models with
# OpenMed's own sha256 integrity verification, and runs the two spike use
# cases against the real models. Honest failure: no network => exit 2,
# never pretend the live run happened.
set -eu
cd "$(dirname "$0")"

echo "== connectivity probe (huggingface.co) =="
if ! curl -sI --max-time 10 https://huggingface.co >/dev/null 2>&1; then
  echo "ERROR: huggingface.co unreachable — live model run cannot start." >&2
  echo "The offline goldens (python3 -m pytest .) remain the pinned reference." >&2
  exit 2
fi

pip install -q "openmed[hf]"

echo "== pin live-verified digests into the manifest =="
# Fetches real LFS sha256s from the HF API, rewrites models-manifest.json
# atomically, re-runs the release gate (must now pass), executes a fixture
# note through the real models, and writes a verification report.
python3 live_verify.py --manifest models-manifest.json

echo "== PII de-identification, real model =="
python3 - <<'PY'
import openmed
res = openmed.deidentify(
    "Amara Okafor (NIN 12345678901, 08035551234) attended Wuse PHC for malaria.",
    method="mask", lang="en")
print(getattr(res, "text", res))
PY

echo "== clinical NER, real model =="
python3 - <<'PY'
import openmed
res = openmed.extract_clinical_entities("malaria fever, gave amodiaquine 200mg")
for e in (getattr(res, "entities", res) or []):
    print(getattr(e, "label", "?"), getattr(e, "text", e))
PY

echo "live model path OK — compare with packages/mednlp goldens"
