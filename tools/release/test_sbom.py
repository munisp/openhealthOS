import json, subprocess, sys, os, tempfile
from sbom import build_sbom, license_gate, hash_manifest

REPO = os.path.join(os.path.dirname(__file__), "..", "..")

def test_sbom_covers_go_modules():
    sbom = build_sbom(REPO)
    assert sbom["bomFormat"] == "CycloneDX" and sbom["components"]

def test_license_gate_blocks_sspl():
    bad = license_gate({"dep-a": "SSPL-1.0", "dep-b": "MIT"})
    assert bad == ["dep-a: SSPL-1.0"]
    assert license_gate({"dep-b": "MIT"}) == []

def test_hash_manifest_deterministic(tmp_path):
    f = tmp_path / "a.txt"; f.write_text("hello")
    m1 = hash_manifest(str(tmp_path), ["*.txt"])
    m2 = hash_manifest(str(tmp_path), ["*.txt"])
    assert m1 == m2 and "a.txt" in m1

def test_cli():
    p = subprocess.run([sys.executable, "sbom.py", REPO], capture_output=True, text=True,
                       cwd=os.path.dirname(__file__))
    assert p.returncode == 0 and json.loads(p.stdout)["components"] > 0
