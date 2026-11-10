"""anchorproof-demo@1.0.0 — the external-notary evidence surface.

Proves, on a real auditlake chain, the gap auditlake's own comments
name — an attacker with write access can rewrite the WHOLE chain and
the local anchors file, and local verification passes — and that the
external notary receipt catches what the local file cannot.

Same doctrines as every demo before it: fixed clock, fixed chain
content, byte-identical reruns, sha256-stamped payload, tempdir paths
never enter the payload, and the registry is snapshot/restored inside
run_demo so reruns are identical in any process.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..", "auditlake")))

import auditlake
import ledgeranchor
from ledgeranchor import (AnchorError, NOTARY_BACKENDS, publish_root,
                          ratify_backend, verify_external)

DEMO_VERSION = "anchorproof-demo@1.0.0"
CLOCK = lambda: "2026-11-10T10:00:00Z"

OUT = os.path.join(os.path.dirname(__file__), "..", "..", "apps",
                   "pwa", "anchorproof.json")

CHAIN = [
    ("consent-service", "issue", "patient-sim-001"),
    ("shr-service", "break-glass", "patient-sim-001"),
    ("claims", "decide", "claim-sim-0007"),
    ("consent-service", "revoke", "patient-sim-001"),
    ("audit", "export", "daily-digest"),
]


def _stamp(payload: dict) -> dict:
    body = {k: v for k, v in payload.items() if k != "sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**payload, "sha256": digest}


def _build_chain(path, *, prefix):
    """A real auditlake chain with injected (fixed) timestamps — the
    chain law is auditlake's own: hash = sha256(canonical + prev)."""
    prev = auditlake.GENESIS
    for i, (actor, action, subject) in enumerate(CHAIN, start=1):
        entry = {"seq": i, "ts": CLOCK(), "actor": actor,
                 "action": action, "subject": prefix + subject,
                 "details": {}, "source_id": f"{actor}-{i}",
                 "prev_hash": prev}
        entry["hash"] = auditlake.entry_hash(entry)
        prev = entry["hash"]
        with open(path, "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


def _root(path, n=None):
    entries = auditlake.read_log(path)
    if n is not None:
        entries = entries[:n]
    return auditlake.merkle_root([e["hash"] for e in entries])


def _attempt(fn):
    try:
        return {"ok": True, "result": fn()}
    except AnchorError as exc:
        return {"ok": False, "refused": str(exc)}


def run_demo():
    # The demo ratifies the fabric template — snapshot/restore the
    # process-global registry so reruns are byte-identical (v1q lesson).
    saved = {k: {**v, "ratified_by": list(v["ratified_by"])}
             for k, v in NOTARY_BACKENDS.items()}
    try:
        with tempfile.TemporaryDirectory() as td:
            chain = os.path.join(td, "audit.log")
            local_anchors = os.path.join(td, "anchors.local")
            offbox = os.path.join(td, "anchors.offbox")

            # --- Act 1: the honest chain, anchored externally ---------
            _build_chain(chain, prefix="")
            root1 = _root(chain)
            receipt = publish_root(root1, backend="file-publication@1.0.0",
                                   entries=len(CHAIN), ts=CLOCK(),
                                   sink=offbox)

            # --- Act 2: the attacker rewrites EVERYTHING on-box -------
            rewritten = os.path.join(td, "rewritten.log")
            _build_chain(rewritten, prefix="REWRITTEN-")
            root2 = _root(rewritten)
            # ...and recomputes the local anchors file to match.
            with open(local_anchors, "w") as fh:
                fh.write(json.dumps({"ts": CLOCK(), "entries": len(CHAIN),
                                     "root": root2}, sort_keys=True)
                         + "\n")
            local_ok, local_msg = auditlake.verify_anchor(rewritten,
                                                          local_anchors)

            # --- Act 3: the external receipt settles it ---------------
            ext_rewritten = verify_external(chain_root=root2,
                                            receipts=[receipt])
            ext_genuine = verify_external(chain_root=root1,
                                          receipts=[receipt])
            unanchored = verify_external(chain_root=root1, receipts=[])

            # --- Act 4: the fabric template, four-eyes ----------------
            t_template = _attempt(lambda: publish_root(
                root1, backend="fabric-gateway@1.0.0",
                entries=len(CHAIN), ts=CLOCK()))
            ratify_backend("fabric-gateway@1.0.0", by="ada-ministry")
            t_one = _attempt(lambda: publish_root(
                root1, backend="fabric-gateway@1.0.0",
                entries=len(CHAIN), ts=CLOCK()))
            ratify_backend("fabric-gateway@1.0.0", by="bola-payer")
            t_no_wire = _attempt(lambda: publish_root(
                root1, backend="fabric-gateway@1.0.0",
                entries=len(CHAIN), ts=CLOCK()))
            fabric_receipt = publish_root(
                root1, backend="fabric-gateway@1.0.0",
                entries=len(CHAIN), ts=CLOCK(),
                transport=lambda **kw: {"tx": "SIM-TX-0001"})

            scenario = {
                "chain_entries": len(CHAIN),
                "genuine_root": root1,
                "rewritten_root": root2,
                "external_receipt": receipt,
                "rewrite_attack": {
                    "local_verify_after_rewrite": {
                        "passed": local_ok, "message": local_msg,
                        "note": "the attacker recomputed the local "
                                "anchors file too — local verification "
                                "passes; this is auditlake's own stated "
                                "residual, demonstrated not asserted"},
                    "external_verdict": ext_rewritten,
                },
                "control_genuine": ext_genuine,
                "unanchored": unanchored,
            }
            fabric = {
                "template_refusal": t_template,
                "one_approver_still_refused": t_one,
                "ratified_no_wire_refusal": t_no_wire,
                "ratified_with_wire": fabric_receipt,
            }

        payload = {
            "version": DEMO_VERSION,
            "generated_at": CLOCK(),
            "scenario": scenario,
            "fabric": fabric,
            "backends": {k: {"kind": v["kind"], "status": v["status"],
                             "ratified_by": list(v["ratified_by"]),
                             "description": v["description"]}
                         for k, v in saved.items()},
            "flags": {
                "rewrite_passes_local": local_ok,
                "rewrite_caught_externally":
                    ext_rewritten["status"] == "history-rewritten",
                "genuine_anchored":
                    ext_genuine["status"] == "externally-anchored",
                "unanchored_stated":
                    unanchored["status"] == "unanchored",
                "template_four_eyes":
                    (not t_template["ok"] and not t_one["ok"]
                     and not t_no_wire["ok"]
                     and fabric_receipt.get("tx") == "SIM-TX-0001"),
            },
            "versions": {"ledgeranchor": ledgeranchor.LEDGERANCHOR_VERSION,
                         "demo": DEMO_VERSION},
        }
        return _stamp(payload)
    finally:
        NOTARY_BACKENDS.clear()
        NOTARY_BACKENDS.update(saved)


def main():
    payload = run_demo()
    out = os.path.normpath(OUT)
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")
    print(f"wrote {out} sha256={payload['sha256'][:12]}…")


if __name__ == "__main__":
    main()
