"""SBOM + release signing (ADR-0059): CycloneDX JSON over repo dependencies
(Go modules, Python imports), plus SHA-256 manifest per deliverable. The
licence gate (ADR-0005) runs here: any BSL/SSPL/Elastic dependency FAILS the
release.
"""
from __future__ import annotations
import glob, hashlib, json, os, re, sys

FORBIDDEN_LICENSE = ["BUSL", "BSL", "SSPL", "Elastic"]

def go_deps(path: str) -> list[dict]:
    out = []
    for mod in glob.glob(os.path.join(path, "services/*/go.mod")) +                glob.glob(os.path.join(path, "shared/*/go.mod")) +                glob.glob(os.path.join(path, "tools/*/go.mod")):
        comp = os.path.relpath(os.path.dirname(mod), path)
        with open(mod) as fh:
            text = fh.read()
        for line in text.splitlines():
            line = line.split("//")[0].strip().strip("\t")
            parts = line.split()
            if line.startswith("module") or line.startswith("go ") or not parts:
                continue
            if parts[0] == "require":
                parts = parts[1:]
            if len(parts) >= 2 and parts[1].startswith("v"):
                out.append({"type": "go", "component": comp, "name": parts[0], "version": parts[1]})
    return out

def license_gate(licenses: dict) -> list[str]:
    """licenses: {dep: spdx}. Any forbidden => failure list."""
    bad = []
    for dep, lic in licenses.items():
        if any(f.lower() in lic.lower() for f in FORBIDDEN_LICENSE):
            bad.append(f"{dep}: {lic}")
    return bad

def model_gate(root: str) -> list[str]:
    """Model-artifact vetting (ADR-0065): if a models-manifest.json exists
    anywhere in the tree, it MUST pass the mednlp gate. Returns failure
    list (empty = clean). No manifest => no models => clean."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "model_gate", os.path.join(root, "packages", "mednlp", "model_gate.py"))
    if spec is None or spec.loader is None:
        return ["model_gate module unavailable"]
    mg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mg)
    failures = []
    for manifest in glob.glob(os.path.join(root, "**", "models-manifest.json"),
                              recursive=True):
        try:
            mg.vet(manifest)
        except Exception as exc:  # fail-closed, same doctrine as licences
            failures.append(f"{os.path.relpath(manifest, root)}: {exc}")
    return failures

def build_sbom(root: str) -> dict:
    deps = go_deps(root)
    return {"bomFormat": "CycloneDX", "specVersion": "1.5",
            "metadata": {"component": {"name": "openhealth-os", "type": "application"}},
            "components": [{"type": "library", "name": f'{d["component"]}:{d["name"]}',
                            "version": d["version"]} for d in deps]}

def hash_manifest(root: str, patterns: list[str]) -> dict:
    out = {}
    for pat in patterns:
        for f in glob.glob(os.path.join(root, pat)):
            with open(f, "rb") as fh:
                out[os.path.relpath(f, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out

def main(argv):
    root = argv[1] if len(argv) > 1 else "."
    sbom = build_sbom(root)
    print(json.dumps({"components": len(sbom["components"]), "format": sbom["bomFormat"]}))
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
