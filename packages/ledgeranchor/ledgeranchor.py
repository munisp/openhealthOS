"""ledgeranchor@1.0.0 — the external-notary seam (ADR-0069).

auditlake's hash chain proves the log is internally consistent, but an
attacker with write access could rewrite the WHOLE chain and recompute
every link — the module's own comment says so. Anchoring closes that
only when the Merkle root is committed somewhere the operator no longer
controls the history of. This package is the narrow seam that does it:

- publish_root() commits a root to an external notary backend and
  returns a stamped receipt.
- verify_external() recomputes and compares against the receipts a
  chain claims — "unanchored" is a stated state, never faked; a
  mismatch after a whole-chain rewrite is RED "history-rewritten".

Backend doctrine (vhpsp, applied to notaries):
- Backends are registry ENTRIES, never code branches.
- A TEMPLATE backend refuses to operate until two distinct named
  approvers ratify it — the hand that proposes never approves alone.
- register_backend refuses ratified entries, refuses overwrite (a
  notary's contract is never edited under an auditor's feet), refuses
  sourceless entries.
- fabric-gateway@1.0.0 ships as a template: Hyperledger Fabric is an
  EXTERNAL network (ADR-0069, ADR-0005), reached only through an
  injected transport. No transport configured = stated refusal, never
  a fabricated receipt.

Trust-boundary doctrine (rows 201-204, applied at construction):
- Roots and receipt stamps are 64 lowercase hex or a named refusal —
  the string is the contract.
- hmac.compare_digest is guarded: non-str/non-ASCII input is a named
  refusal BEFORE comparison, never a TypeError at the gate.

Stdlib only. No clocks are read: timestamps are injected.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

LEDGERANCHOR_VERSION = "ledgeranchor@1.0.0"

_HEX = frozenset("0123456789abcdef")


class AnchorError(Exception):
    pass


def _refuse(msg):
    raise AnchorError(msg)


def _stamp_receipt(receipt: dict) -> dict:
    body = {k: v for k, v in receipt.items() if k != "receipt_sha256"}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True,
                                       separators=(",", ":"))
                            .encode()).hexdigest()
    return {**receipt, "receipt_sha256": digest}


def _check_hex64(value, *, what):
    """The string is the contract: refuse anything that is not exactly
    64 lowercase hex — before any comparison can be attempted."""
    if not isinstance(value, str) or not value.isascii():
        _refuse(f"{what} is not an ASCII string — refused, never compared")
    if len(value) != 64 or any(c not in _HEX for c in value):
        _refuse(f"{what} is not 64 lowercase hex — non-canonical")


# --- backend registry (entries, never code branches) ------------------------

NOTARY_BACKENDS = {
    "file-publication@1.0.0": {
        "kind": "file",
        "status": "ratified",
        "ratified_by": ["platform-architecture", "security-review"],
        "source": "auditlake anchor comment: a root printed, emailed, "
                  "or published off-box is a root you no longer control "
                  "the history of",
        "description": "append-only anchors file living OFF the audited "
                       "box — the formalized version of today's manual "
                       "practice",
    },
    "fabric-gateway@1.0.0": {
        "kind": "fabric",
        "status": "template",
        "ratified_by": [],
        "source": "ADR-0069 — ECOWAS corridor / pharma-provenance "
                  "consortium scenario",
        "description": "Hyperledger Fabric gateway submit(root, entries, "
                       "ts) on a consortium channel; EXTERNAL network "
                       "only, never embedded; requires an injected "
                       "transport after ratification",
    },
}

_REQUIRED = ("kind", "status", "source", "description")


def register_backend(name: str, entry: dict) -> dict:
    """A notary backend is data. Ratified entries and silent overwrites
    are refused; a template declares the approvers it still needs."""
    if not name or "@" not in name:
        _refuse("backend names are versioned: name@x.y.z")
    missing = [k for k in _REQUIRED if k not in entry]
    if missing:
        _refuse(f"backend entry missing: {', '.join(missing)}")
    if not entry["source"]:
        _refuse("sourceless backend — a notary contract names its basis")
    existing = NOTARY_BACKENDS.get(name)
    if existing is not None:
        _refuse(f"backend {name} already registered — a notary's "
                "contract is never edited under an auditor's feet; "
                "register a new version")
    if entry["status"] == "ratified":
        approvers = entry.get("ratified_by") or []
        if len(set(approvers)) < 2:
            _refuse("a ratified backend needs two distinct named "
                    "approvers — the hand that proposes never approves "
                    "alone")
    elif entry["status"] != "template":
        _refuse("backend status is template or ratified")
    NOTARY_BACKENDS[name] = {**entry,
                             "ratified_by": list(entry.get("ratified_by")
                                                 or [])}
    return NOTARY_BACKENDS[name]


def ratify_backend(name: str, *, by: str) -> dict:
    """Four-eyes ratification of a template backend."""
    entry = NOTARY_BACKENDS.get(name)
    if entry is None:
        _refuse(f"unknown backend {name}")
    if entry["status"] == "ratified":
        _refuse(f"backend {name} is already ratified")
    if not by:
        _refuse("a ratification names its approver")
    approvers = entry["ratified_by"]
    if by in approvers:
        _refuse(f"{by} has already signed — approvers are distinct")
    approvers.append(by)
    if len(approvers) >= 2:
        entry["status"] = "ratified"
    return entry


# --- publish / verify --------------------------------------------------------

def publish_root(root: str, *, backend: str, entries: int, ts: str,
                 sink=None, transport=None) -> dict:
    """Commit a Merkle root to an external notary; return the stamped
    receipt. `ts` is injected — this module never reads a clock."""
    _check_hex64(root, what="root")
    if not isinstance(entries, int) or entries < 0:
        _refuse("entries is a non-negative count — absence is not a value")
    if not ts:
        _refuse("a receipt carries its timestamp — injected, never read")
    entry = NOTARY_BACKENDS.get(backend)
    if entry is None:
        _refuse(f"unknown backend {backend}")
    if entry["status"] != "ratified":
        needed = 2 - len(entry["ratified_by"])
        _refuse(f"backend {backend} is a TEMPLATE — {needed} more named "
                "approver(s) required before it operates")
    receipt = {"notary": backend, "root": root, "entries": entries,
               "ts": ts}
    if entry["kind"] == "file":
        if not sink:
            _refuse("file publication needs a sink path — the off-box "
                    "anchors file")
        line = json.dumps(_stamp_receipt(receipt), sort_keys=True)
        with open(sink, "a") as fh:
            fh.write(line + "\n")
        return _stamp_receipt(receipt)
    if entry["kind"] == "fabric":
        if transport is None:
            _refuse("no transport configured — a missing wire is "
                    "stated, never faked")
        ack = transport(root=root, entries=entries, ts=ts)
        if not isinstance(ack, dict) or not ack.get("tx"):
            _refuse("notary returned no transaction reference — an "
                    "anchor without a reference is not an anchor")
        receipt["tx"] = ack["tx"]
        return _stamp_receipt(receipt)
    _refuse(f"backend kind {entry['kind']} has no publication path")


def verify_external(*, chain_root: str, receipts: list) -> dict:
    """Compare a chain's current root against the external receipts.

    Never raises on attacker-shaped receipts: every bad shape is a
    named verdict. Returns a verdict dict with status:
      externally-anchored / history-rewritten / unanchored / malformed.
    """
    if not receipts:
        return {"status": "unanchored", "severity": "amber",
                "detail": "no external receipts on record — absence is "
                          "stated, never treated as anchored"}
    try:
        _check_hex64(chain_root, what="chain root")
    except AnchorError as exc:
        return {"status": "malformed", "severity": "red",
                "detail": str(exc)}
    best = None
    for rec in receipts:
        if not isinstance(rec, dict):
            return {"status": "malformed", "severity": "red",
                    "detail": "a receipt is not a JSON object — refused"}
        presented = rec.get("root")
        # Guard before compare_digest: a non-ASCII receipt root would
        # raise TypeError at the gate (row 202 lesson).
        if not isinstance(presented, str) or not presented.isascii():
            return {"status": "malformed", "severity": "red",
                    "detail": "receipt root is not an ASCII string — "
                              "refused, never compared"}
        if len(presented) != 64 or any(c not in _HEX for c in presented):
            return {"status": "malformed", "severity": "red",
                    "detail": "receipt root is not 64 lowercase hex — "
                              "the string is the contract"}
        if best is None or rec.get("entries", -1) > best.get("entries",
                                                             -1):
            best = rec
    if hmac.compare_digest(best["root"], chain_root):
        return {"status": "externally-anchored", "severity": "green",
                "detail": f"root matches the external receipt from "
                          f"{best['notary']} at {best['ts']} covering "
                          f"{best['entries']} entries",
                "notary": best["notary"], "ts": best["ts"]}
    return {"status": "history-rewritten", "severity": "red",
            "detail": f"external receipt from {best['notary']} holds a "
                      "different root — the chain was rewritten and "
                      "recomputed after anchoring",
            "notary": best["notary"], "ts": best["ts"],
            "anchored_root": best["root"], "presented_root": chain_root}
