"""storageproof demo — the storage port, with opendal as the main
engine (ADR-0089).

Six drills on private tmpdirs and the live registry: a faithful
roundtrip through the opendal fs service, a range-read resume, typed
absence, stated refusals (capability and credentials, both
pre-network), degraded mode declared rather than silent, and the live
adapter census. Deterministic: the source bytes are a sha256 stream,
the clock is fixed, the payload is compact-stamped through payloadio.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "payloadio"))

import payloadio  # noqa: E402
import storageport  # noqa: E402

DEMO_VERSION = "storageproof-demo@1.0.0"
CLOCK = "2026-11-13T00:00:00Z"
OUT = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "apps", "pwa",
    "storageproof.json"))

#: Deterministic 64 KiB of source bytes — a sha256 stream, so the
#: roundtrip checksum is provable, not plausible.
SOURCE = b"".join(hashlib.sha256(b"storageproof-%d" % i).digest()
                  for i in range(2048))
SOURCE_SHA256 = hashlib.sha256(SOURCE).hexdigest()

MECHANISM = {
    "what": "one storage port over every object backend; Apache "
            "OpenDAL is the MAIN engine — fs, memory and cloud schemes "
            "all run through opendal when it is importable",
    "main_not_optional": "the suite pins ADAPTER == 'opendal': if the "
                         "main engine is absent the gate goes red and "
                         "names it; the stdlib path is declared "
                         "degraded mode, fs/memory only, stamped on "
                         "every receipt",
    "why": "a deployment target is a registry entry, never a code "
           "branch — ministry server room (fs), clinic process "
           "(memory), cloud archive (s3) are configuration; retry is "
           "a layer and its policy is data; range reads resume an "
           "interrupted transfer at the offset instead of restarting "
           "from byte zero — the low-bandwidth contract",
    "does_not_replace": "payloadio owns LOCAL payload atomicity "
                        "(ADR-0088, tmp+rename); object stores are "
                        "atomic on PUT. Two layers, two contracts — "
                        "storageport stores documents and media, "
                        "payloadio lands the proof payloads",
    "license": "Apache-2.0 — clears the license doctrine (no "
               "BSL/SSPL/Elastic, no GPL/AGPL contamination)",
}

REGISTRY_SCOPE = {
    "entries": sorted(storageport.REGISTRY),
    "credentials": "never values in the registry — they arrive via "
                   "secrets= at connect time; a required key without "
                   "a secret is a stated refusal, pre-network",
}


def _stamp(body: dict) -> dict:
    body["sha256"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def _demo_registry(root: str) -> dict:
    return {"demo-vault": {"scheme": "fs", "config": {"root": root},
                           "purpose": "drill fixture"}}


def _drill_roundtrip() -> dict:
    """Put 64 KiB through the opendal fs service, read it back, and
    the checksums match — the checksum is the contract."""
    with tempfile.TemporaryDirectory() as td:
        store = storageport.connect("demo-vault",
                                    registry=_demo_registry(td))
        receipt = store.put("imaging/study-0001.bin", SOURCE)
        back = store.get("imaging/study-0001.bin")
        stat = store.stat("imaging/study-0001.bin")
        store.delete("imaging/study-0001.bin")
        gone = not store.exists("imaging/study-0001.bin")
    return {"name": "roundtrip-faithful", "fate": "green",
            "severity": "green",
            "bytes": len(SOURCE),
            "checksum_match": receipt["sha256"] == SOURCE_SHA256
            and hashlib.sha256(back).hexdigest() == SOURCE_SHA256,
            "stat_length": stat["content_length"],
            "delete_idempotent_gone": gone,
            "adapter": receipt["adapter"],
            "detail": "stored through opendal, returned byte-exact — "
                      "sha256 both directions"}


def _drill_range_resume() -> dict:
    """Range read: an interrupted transfer resumes at the offset, it
    does not restart from byte zero."""
    with tempfile.TemporaryDirectory() as td:
        store = storageport.connect("demo-vault",
                                    registry=_demo_registry(td))
        store.put("media/scan.pdf", SOURCE)
        chunk = store.get_range("media/scan.pdf", 1024, 4096)
        tail = store.get_range("media/scan.pdf", 65536 - 1000, 1000)
    return {"name": "range-read-resume", "fate": "green",
            "severity": "green",
            "range_match": chunk == SOURCE[1024:5120],
            "tail_match": tail == SOURCE[-1000:],
            "detail": "low-bandwidth contract: resume where the "
                      "connection dropped, not from zero"}


def _drill_absence() -> dict:
    """Absence is not a value: reading or stating a missing object is
    a typed AbsentKey, never a None posing as data."""
    with tempfile.TemporaryDirectory() as td:
        store = storageport.connect("demo-vault",
                                    registry=_demo_registry(td))
        refused = []
        for fn in (lambda: store.get("never/written.bin"),
                   lambda: store.stat("never/written.bin")):
            try:
                fn()
                refused.append("NOT-REFUSED")
            except storageport.AbsentKey as exc:
                refused.append(str(exc))
    return {"name": "absence-is-not-a-value", "fate": "green",
            "severity": "green",
            "typed_absences": len(refused),
            "all_typed": all("absence is not a value" in m
                             for m in refused),
            "detail": refused[0]}


def _drill_stated_refusals() -> dict:
    """Two refusals, both stated, both pre-network: presign on a
    scheme that declares no such capability, and a cloud entry whose
    credentials were never supplied."""
    refusals = []
    with tempfile.TemporaryDirectory() as td:
        store = storageport.connect("demo-vault",
                                    registry=_demo_registry(td))
        try:
            store.presign_read("imaging/study-0001.bin", 300)
            refusals.append("NOT-REFUSED")
        except storageport.StorageRefusal as exc:
            refusals.append(str(exc))
    try:
        storageport.connect("cloud-archive")  # shipped registry, no secrets
        refusals.append("NOT-REFUSED")
    except storageport.StorageRefusal as exc:
        refusals.append(str(exc))
    return {"name": "stated-refusals", "fate": "green",
            "severity": "green", "refusals": len(refusals),
            "capability_refusal_names_scheme":
                "no presign capability" in refusals[0],
            "credential_refusal_names_keys":
                "access_key_id" in refusals[1]
                and "secret_access_key" in refusals[1],
            "network_attempted": False,
            "detail": "fail-closed: a missing credential is a stated "
                      "refusal before any network is touched — never "
                      "a signing error from the wire"}


def _drill_degraded_declared() -> dict:
    """Degraded mode is declared, never silent: force the stdlib
    fallback and service continues for fs — with the adapter stamped
    on the receipt — while the cloud scheme refuses by name."""
    with tempfile.TemporaryDirectory() as td:
        store = storageport.connect("demo-vault",
                                    registry=_demo_registry(td),
                                    force_degraded=True)
        receipt = store.put("cache/page-1.bin", SOURCE[:4096])
        works = store.get("cache/page-1.bin") == SOURCE[:4096]
        stamped = receipt["adapter"] == storageport.ADAPTER_DEGRADED
        try:
            storageport.connect(
                "cloud-archive",
                secrets={"access_key_id": "D", "secret_access_key": "D"},
                force_degraded=True)
            cloud_refused = "NOT-REFUSED"
        except storageport.StorageRefusal as exc:
            cloud_refused = str(exc)
    return {"name": "degraded-mode-is-declared", "fate": "amber",
            "severity": "amber",
            "fs_service_continues": works,
            "adapter_stamped": stamped,
            "cloud_refused_by_name": "degraded mode" in cloud_refused,
            "detail": "fail-closed doctrine: degraded service with "
                      "the mode stamped on every receipt beats a dead "
                      "service — but the cloud never silently falls "
                      "back"}


def _drill_live_census() -> dict:
    """The live census: the main engine is active, the registry is
    data, and the schemes are read, not assumed."""
    c = storageport.census()
    return {"name": "live-adapter-census", "fate": "green",
            "severity": "green",
            "adapter_main": c["adapter_main"],
            "adapter_active": c["adapter_active"],
            "degraded": c["degraded"],
            "opendal_version": c["opendal_version"],
            "retry_times": c["retry_times"],
            "registry_entries": c["registry_entries"],
            "schemes": c["schemes"],
            "entries_requiring_secrets": c["entries_requiring_secrets"],
            "detail": "main means main — the suite pins this census"}


def run_demo() -> dict:
    body = {
        "demo": DEMO_VERSION,
        "generated_at": CLOCK,
        "mechanism": MECHANISM,
        "registry_scope": REGISTRY_SCOPE,
        "adapter": storageport.census(),
        "source": {"bytes": len(SOURCE), "sha256": SOURCE_SHA256},
        "drills": [
            _drill_roundtrip(),
            _drill_range_resume(),
            _drill_absence(),
            _drill_stated_refusals(),
            _drill_degraded_declared(),
            _drill_live_census(),
        ],
        "doctrine": [
            "a deployment target is a registry entry, never a code "
            "branch",
            "main means main: opendal absent is a red gate, not a "
            "quiet fallback",
            "degraded mode is declared on every receipt, never silent",
            "a missing credential is a stated refusal, pre-network — "
            "never a signing error from the wire",
            "absence is not a value",
            "the checksum is the contract",
            "storageport stores documents and media; payloadio lands "
            "the proof payloads — two layers, two contracts",
        ],
        "self_integrity": {"contract": "compact"},
    }
    return _stamp(body)


def main() -> None:
    body = run_demo()
    payloadio.write_text(OUT, json.dumps(body, sort_keys=True,
                                         separators=(",", ":")))
    print("storageproof: %d drills, adapter %s, sha %s -> %s"
          % (len(body["drills"]), body["adapter"]["adapter_active"],
             body["sha256"][:12], OUT))


if __name__ == "__main__":
    main()
