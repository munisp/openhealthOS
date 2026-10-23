"""NHIA submission conformance (ADR-0057): builds and validates capitation
and fee-for-service submission files in the NHIA-style envelope. The format
is expressed as data (required fields, tariff-code validation hook) so state
variations layer on without code forks.
"""
from __future__ import annotations
import hashlib, json, sys

REQUIRED_ENVELOPE = ["scheme", "facility_id", "period", "claims"]
REQUIRED_CLAIM_FFS = ["member_id", "encounter_id", "tariff_code", "amount", "date"]
REQUIRED_CLAIM_CAP = ["member_id", "period", "capitation_rate"]

class NhiaError(Exception):
    pass

def validate_envelope(sub: dict) -> list[str]:
    issues = []
    for f in REQUIRED_ENVELOPE:
        if f not in sub or sub[f] in (None, ""):
            issues.append(f"missing envelope field: {f}")
    if sub.get("scheme") not in ("NHIA-FFS", "NHIA-CAP", "STATE-HMO"):
        issues.append(f"unknown scheme: {sub.get('scheme')}")
    return issues

def validate_claim(claim: dict, capitation: bool, tariff_check=None) -> list[str]:
    req = REQUIRED_CLAIM_CAP if capitation else REQUIRED_CLAIM_FFS
    issues = [f"missing claim field: {f}" for f in req if f not in claim or claim[f] in (None, "")]
    if not capitation:
        if "amount" in claim and (not isinstance(claim["amount"], (int, float)) or claim["amount"] < 0):
            issues.append("amount must be a non-negative number")
        if tariff_check and claim.get("tariff_code") and not tariff_check(claim["tariff_code"]):
            issues.append(f"tariff_code not in schedule: {claim['tariff_code']}")
    return issues

def build_submission(scheme: str, facility_id: str, period: str, claims: list[dict],
                     tariff_check=None) -> dict:
    sub = {"scheme": scheme, "facility_id": facility_id, "period": period, "claims": claims}
    issues = validate_envelope(sub)
    cap = scheme == "NHIA-CAP"
    rejected = []
    for i, c in enumerate(claims):
        ci = validate_claim(c, cap, tariff_check)
        if ci:
            rejected.append({"index": i, "claim": c.get("member_id", "?"), "issues": ci})
            issues += [f"claim[{i}]: {x}" for x in ci]
    clean = [c for i, c in enumerate(claims) if all(r["index"] != i for r in rejected)]
    total = sum(c.get("amount", 0) for c in clean) if not cap else             sum(c.get("capitation_rate", 0) for c in clean)
    body = json.dumps({"scheme": scheme, "period": period, "claims": clean}, sort_keys=True)
    return {"submission": {**sub, "claims": clean, "total_amount": total,
                           "submission_hash": hashlib.sha256(body.encode()).hexdigest()[:16]},
            "conformant": not issues, "issues": issues, "rejected_claims": rejected,
            "submitted": len(clean)}

def main(argv):
    doc = json.load(sys.stdin)
    out = build_submission(doc["scheme"], doc["facility_id"], doc["period"], doc["claims"])
    print(json.dumps(out, indent=2))
    return 0 if out["conformant"] else 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
