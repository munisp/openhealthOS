"""live_verify.py — live model verification for the mednlp gate (ADR-0065).

The release model gate refuses any manifest entry whose sha256 pin is not
a verified 64-hex digest. This tool is how a pin BECOMES verified:

  1. Query the HuggingFace API for the model's file list; every artifact
     carries an LFS sha256 (``lfs.oid``) server-side.
  2. Rewrite the manifest entry with the real digest of the weight file,
     stamping ``verified_at`` and the API revision.
  3. Re-run the gate — it must now PASS — then load the model through
     OpenMed (which independently verifies the same hash at download) and
     run de-id + entity extraction on a fixture note.
  4. Write a verification report the release train can archive.

Honest-failure doctrine: no network / no HF API / model missing =>
exit 2 with a loud message. Nothing is ever pinned from assumption.

Usage:
  python3 live_verify.py [--manifest packages/mednlp/models-manifest.json]
"""
import argparse
import datetime
import json
import os
import sys
import urllib.request

HF_API = "https://huggingface.co/api/models/{repo}?files_metadata=true"
WEIGHT_SUFFIXES = (".safetensors", ".bin", ".onnx")
HEX = set("0123456789abcdef")


class LiveVerifyError(Exception):
    pass


def fetch_file_digests(repo, api_base=HF_API, opener=urllib.request.urlopen):
    """Return {filename: sha256} for weight files, straight from the HF API."""
    url = api_base.format(repo=repo)
    try:
        with opener(url, timeout=30) as r:
            meta = json.loads(r.read())
    except Exception as e:  # noqa: BLE001 — network honesty
        raise LiveVerifyError("cannot reach HuggingFace API for %s (%s)"
                              % (repo, type(e).__name__))
    siblings = meta.get("siblings") or []
    digs = {}
    for s in siblings:
        name = s.get("rfilename", "")
        lfs = s.get("lfs") or {}
        oid = lfs.get("oid", "")
        if name.endswith(WEIGHT_SUFFIXES) and len(oid) == 64 \
                and all(c in HEX for c in oid):
            digs[name] = oid
    if not digs:
        raise LiveVerifyError("no pinned weight files found for %s" % repo)
    return digs, meta.get("sha", "")  # repo revision


def pin_manifest(manifest_path, fetcher=fetch_file_digests, now=None):
    """Replace placeholder pins with live-verified digests. Fail-closed."""
    with open(manifest_path) as f:
        manifest = json.load(f)
    now = now or datetime.datetime.now(datetime.timezone.utc).isoformat()
    changed = []
    for entry in manifest.get("models", []):
        digs, rev = fetcher(entry["id"])
        # defense in depth: never pin anything that is not a 64-hex digest,
        # whatever the fetcher returned
        for fname, oid in digs.items():
            if len(oid) != 64 or not all(c in HEX for c in oid):
                raise LiveVerifyError(
                    "fetcher returned an invalid digest for %s (%s) — "
                    "refusing to pin" % (entry["id"], fname))
        # prefer the primary weight file; deterministic choice
        fname = sorted(digs)[0]
        entry["sha256"] = digs[fname]
        entry["weight_file"] = fname
        entry["hf_revision"] = rev
        entry["verified_at"] = now
        changed.append(entry["id"])
    if changed:
        tmp = manifest_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        os.replace(tmp, manifest_path)  # atomic
    return changed


FIXTURE_NOTE = ("Patient NIN 12345678901 (phone 08035551234) presents with "
                "fever and headache; started on artemether-lumefantrine.")


def verify_pipeline(manifest_path):
    """Gate must pass, then OpenMed must run on a fixture note."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_gate import vet  # after pins land this must pass
    vet(manifest_path)
    import openmed
    deid = openmed.deidentify(FIXTURE_NOTE)
    ents = openmed.extract_pii(FIXTURE_NOTE)
    return {"deid_output": deid if isinstance(deid, str) else str(deid),
            "entities_found": len(ents) if hasattr(ents, "__len__") else None,
            "fixture": FIXTURE_NOTE}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "models-manifest.json"))
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)
    try:
        changed = pin_manifest(args.manifest)
        result = verify_pipeline(args.manifest)
    except LiveVerifyError as e:
        sys.stderr.write("live verify: %s\n" % e)
        sys.stderr.write("manifest left untouched; release gate keeps "
                         "refusing unverified pins.\n")
        return 2
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("live verify failed after pinning: %s\n" % e)
        return 2
    report = {"manifest": args.manifest, "pinned": changed, **result}
    out = args.report or args.manifest.replace(".json", "-verify-report.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print("live verify OK: pinned %s; report at %s" % (", ".join(changed), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
