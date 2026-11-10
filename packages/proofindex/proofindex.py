"""proofindex@1.0.0 — the meta-layer for "the checksum is the contract".

Every demo surface on the platform carries a sha256 stamp over its own
canonical body. Until now each board asked the reader to trust that the
stamp matched. proofindex closes that loop: it re-reads every committed
evidence payload, recomputes the stamp, and publishes a verdict per
payload — green "verified", red "drift", or a named refusal.

Doctrines enforced here:

- The checksum is the contract — and the contract covers the STRING,
  not just the bytes (row 201). A stamp that is not 64 lowercase hex
  characters is "non-canonical-stamp", a named verdict, never a silent
  mismatch.
- Fail closed, never crash (row 202): a missing file is "absent", a
  corrupt file is "malformed", a missing stamp is "unstamped", a
  non-ASCII stamp is "malformed-stamp" — all stated verdicts. The
  hmac.compare_digest TypeError hole is guarded at construction time:
  the presented stamp is proven str+ASCII before any comparison.
- Absence is not a value: a payload that is not on disk is reported
  absent, never treated as verified, never treated as drift.
- Two canonicalization contracts exist in the estate, pinned here as
  data, not discovered by accident:
    "compact"      — json.dumps(body, sort_keys=True,
                                 separators=(",", ":"))          [ensure_ascii=True]
    "spaced-utf8"  — json.dumps(body, sort_keys=True,
                                ensure_ascii=False)              [default separators]
  A payload that verifies under a DIFFERENT contract than its registry
  entry records is "contract-mismatch" (amber): the evidence is intact
  but the registry is stale — stated, never silent.
- The index cannot stamp its own stamp. proofindex.json carries a
  compact-contract stamp over everything except the sha256 key; its
  integrity is pinned by the determinism + stamp-contract tests in
  test_proofindex.py, and it deliberately does NOT appear in PAYLOADS.

Stdlib only (ADR-0005). No clocks are read here — verify_all takes the
timestamp from its caller.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os

PROOFINDEX_VERSION = "proofindex@1.0.0"

_HEX = frozenset("0123456789abcdef")


def _canon_compact(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"))
        .encode()).hexdigest()


def _canon_spaced_utf8(body: dict) -> str:
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, ensure_ascii=False)
        .encode()).hexdigest()


#: The two stamp contracts in the estate, as data. Any future payload
#: must pick one and register below — a third variant is a defect.
CONTRACTS = {
    "compact": _canon_compact,
    "spaced-utf8": _canon_spaced_utf8,
}

#: Every stamped evidence payload in the tree, in stable display order.
#: path is repo-relative; contract is the one its exporter uses;
#: exporter is the module that produces it (evidence provenance).
PAYLOADS = [
    {"path": "apps/pwa/tamperproof.json", "contract": "compact",
     "exporter": "packages/travelpass/demo_tamper.py",
     "title": "Tamper-gate evidence"},
    {"path": "apps/pwa/deployproof.json", "contract": "compact",
     "exporter": "packages/travelpass/demo_deploy.py",
     "title": "Deployment-seam proof"},
    {"path": "apps/pwa/agentwrite.json", "contract": "compact",
     "exporter": "packages/agentwrite/demo_agentwrite.py",
     "title": "Agent-write stack"},
    {"path": "apps/pwa/appmarket.json", "contract": "compact",
     "exporter": "packages/appmarket/demo_appmarket.py",
     "title": "App marketplace"},
    {"path": "apps/pwa/charting.json", "contract": "compact",
     "exporter": "packages/charting/demo_charting.py",
     "title": "Clinical charting"},
    {"path": "apps/patient/record.json", "contract": "compact",
     "exporter": "packages/ips/demo_portal.py",
     "title": "Patient record portal"},
    {"path": "apps/pwa/vhc-br10.json", "contract": "compact",
     "exporter": "packages/travelpass/vhdash.py",
     "title": "BR-10 gate dashboard"},
    {"path": "apps/pwa/vhc-fund.json", "contract": "compact",
     "exporter": "packages/travelpass/vhdash.py",
     "title": "Fund dashboard"},
    {"path": "apps/pwa/vhc-helpdesk.json", "contract": "compact",
     "exporter": "packages/travelpass/vhdash.py",
     "title": "Helpdesk dashboard"},
    {"path": "apps/pwa/vhc-growth.json", "contract": "compact",
     "exporter": "packages/travelpass/vhdashx.py",
     "title": "Growth book"},
    {"path": "apps/pwa/inpatient.ng.json", "contract": "spaced-utf8",
     "exporter": "packages/emrdemo/demo_emr.py",
     "title": "Inpatient depth (NG)"},
    {"path": "apps/pwa/inpatient.cv.json", "contract": "spaced-utf8",
     "exporter": "packages/emrdemo/demo_emr.py",
     "title": "Inpatient depth (CV)"},
    {"path": "apps/pwa/specialty.ng.json", "contract": "spaced-utf8",
     "exporter": "packages/emrdemo/demo_emr.py",
     "title": "Specialty depth (NG)"},
    {"path": "apps/pwa/specialty.cv.json", "contract": "spaced-utf8",
     "exporter": "packages/emrdemo/demo_emr.py",
     "title": "Specialty depth (CV)"},
    {"path": "apps/pwa/national.ng.json", "contract": "spaced-utf8",
     "exporter": "packages/natdemo/demo_national.py",
     "title": "National rails (NG)"},
    {"path": "apps/pwa/national.cv.json", "contract": "spaced-utf8",
     "exporter": "packages/natdemo/demo_national.py",
     "title": "National rails (CV)"},
    {"path": "apps/pwa/sync.ng.json", "contract": "spaced-utf8",
     "exporter": "packages/syncmesh/demo_sync.py",
     "title": "Sync depth (NG)"},
    {"path": "apps/pwa/sync.cv.json", "contract": "spaced-utf8",
     "exporter": "packages/syncmesh/demo_sync.py",
     "title": "Sync depth (CV)"},
]

_RED = {"drift", "absent", "malformed", "unstamped", "malformed-stamp",
        "non-canonical-stamp", "unreadable"}
_AMBER = {"contract-mismatch"}


def _verdict(entry: dict, status: str, **extra) -> dict:
    return {"path": entry["path"], "title": entry["title"],
            "contract": entry["contract"], "exporter": entry["exporter"],
            "status": status, "severity": ("red" if status in _RED else
                                           "amber" if status in _AMBER
                                           else "green"), **extra}


def verify_dict(payload: object, entry: dict) -> dict:
    """Verify an already-parsed payload against its registry entry.

    Never raises on attacker-shaped input — every bad shape is a named
    verdict. This is the trust gate; behave like one.
    """
    if not isinstance(payload, dict):
        return _verdict(entry, "malformed",
                        detail="payload is not a JSON object")
    presented = payload.get("sha256")
    if presented is None:
        return _verdict(entry, "unstamped",
                        detail="no sha256 key — absence is not a value")
    # Guard BEFORE compare_digest: non-str or non-ASCII would raise
    # TypeError at the gate (row 202 lesson, applied at construction).
    if not isinstance(presented, str) or not presented.isascii():
        return _verdict(entry, "malformed-stamp",
                        detail="stamp is not an ASCII string — refused, "
                               "never compared")
    if (len(presented) != 64
            or any(c not in _HEX for c in presented)):
        return _verdict(entry, "non-canonical-stamp",
                        detail="stamp is not 64 lowercase hex — the "
                               "string is the contract")
    body = {k: v for k, v in payload.items() if k != "sha256"}
    matches = [name for name, fn in CONTRACTS.items()
               if hmac.compare_digest(fn(body), presented)]
    if not matches:
        return _verdict(entry, "drift",
                        detail="recomputed digest matches neither "
                               "contract — the bytes changed after "
                               "stamping",
                        presented=presented)
    if entry["contract"] not in matches:
        return _verdict(entry, "contract-mismatch",
                        detail="verifies under %s but the registry "
                               "records %s — evidence intact, registry "
                               "stale" % (matches[0], entry["contract"]),
                        presented=presented,
                        verifies_under=matches[0])
    return _verdict(entry, "verified",
                    detail="sha256 matches the recorded %s contract"
                           % entry["contract"],
                    presented=presented,
                    payload_version=payload.get("version"))


def verify_payload(entry: dict, *, root: str = ".") -> dict:
    """Read and verify one registered payload from disk. Never raises:
    absence and corruption are verdicts, not exceptions."""
    full = os.path.join(root, entry["path"])
    if not os.path.exists(full):
        return _verdict(entry, "absent",
                        detail="file not found — absence is stated, "
                               "never faked")
    try:
        with open(full, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return _verdict(entry, "unreadable", detail=str(exc))
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _verdict(entry, "malformed",
                        detail="invalid JSON: %s" % exc)
    return verify_dict(payload, entry)


def verify_all(*, root: str = ".", generated_at: str) -> dict:
    """Verify every registered payload. `generated_at` is injected —
    this module never reads a clock."""
    verdicts = [verify_payload(e, root=root) for e in PAYLOADS]
    reds = [v for v in verdicts if v["severity"] == "red"]
    ambers = [v for v in verdicts if v["severity"] == "amber"]
    return {
        "version": PROOFINDEX_VERSION,
        "generated_at": generated_at,
        "payloads": verdicts,
        "summary": {
            "total": len(verdicts),
            "verified": sum(1 for v in verdicts
                            if v["status"] == "verified"),
            "drift": sum(1 for v in verdicts if v["status"] == "drift"),
            "absent": sum(1 for v in verdicts if v["status"] == "absent"),
            "red": len(reds),
            "amber": len(ambers),
            "all_verified": not reds and not ambers,
        },
        "contracts": {name: sum(1 for e in PAYLOADS
                                if e["contract"] == name)
                      for name in CONTRACTS},
    }
