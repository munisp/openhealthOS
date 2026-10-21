"""model_gate.py — model-artifact licence vetting (ADR-0065).

OpenMed's SDK is Apache-2.0, but model and dataset licences vary per
artifact — their own docs say to validate each one. Our SBOM licence gate
(tools/release/sbom.py) covers code dependencies; this gate covers MODEL
artifacts with the same doctrine:

- every model artifact must be registered in models-manifest.json with
  an explicit licence AND a sha256 pin (OpenMed verifies integrity with
  sha256 on download — we pin the same hash so release-time vetting and
  runtime-verified bytes are provably the same artifact);
- licences outside the allowlist block the release;
- an unregistered model blocks the release. Fail-closed, like SBOM.
"""
import json

ALLOWED_MODEL_LICENCES = {
    "apache-2.0", "mit", "bsd-2-clause", "bsd-3-clause",
    "cc-by-4.0", "cc0-1.0", "odc-by-1.0",
}
# never allowed, mirroring the code-side gate doctrine
BLOCKED_MODEL_LICENCES = {
    "bsl-1.1", "sspl-1.0", "elastic-2.0", "cc-by-nc-4.0", "cc-by-nc-sa-4.0",
    "openrail-nc", "llama-community-nc",
}


class ModelGateError(Exception):
    pass


def vet(manifest_path):
    """Vet a models manifest. Returns the registered models. Raises
    ModelGateError (fail-closed) on any violation."""
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)
    models = manifest.get("models", [])
    if not models:
        raise ModelGateError("empty manifest — no model may ship unregistered")
    problems = []
    for m in models:
        name = m.get("id", "<missing id>")
        lic = (m.get("license") or "").strip().lower()
        if not lic:
            problems.append(f"{name}: no licence declared")
        elif lic in BLOCKED_MODEL_LICENCES:
            problems.append(f"{name}: blocked licence {lic}")
        elif lic not in ALLOWED_MODEL_LICENCES:
            problems.append(f"{name}: unvetted licence {lic}")
        sha = (m.get("sha256") or "").strip().lower()
        if not sha:
            problems.append(f"{name}: no sha256 integrity pin")
        elif not all(c in "0123456789abcdef" for c in sha) or len(sha) != 64:
            problems.append(f"{name}: sha256 pin is not a verified 64-hex digest")
    if problems:
        raise ModelGateError("; ".join(problems))
    return models
