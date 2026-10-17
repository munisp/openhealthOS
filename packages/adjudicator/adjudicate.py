#!/usr/bin/env python3
"""OpenHealth OS — claims adjudication engine.

Reads a claim JSON on stdin, writes a decision JSON on stdout.
Deterministic and auditable: every rule that fires returns a reason or flag
code — adjudications are events, never silent verdicts.

Clean-room re-implementation of the adjudication pipeline pattern
(ADR-0005): eligibility → benefit → tariff → duplicate/fraud flags →
APPROVE / PEND / DENY. Registries (members, benefits, tariffs) are in-file
fixtures here; production reads them from the FHIR/claims services over
Dapr/Kafka.
"""
import json
import sys
from datetime import datetime

MEMBERS = {
    "NG-0001": {"name": "Amina Yusuf", "scheme": "BHCPF-VUL", "active": True, "valid_until": "2027-12-31", "copay_waiver": True},
    "NG-0002": {"name": "Chinedu Okafor", "scheme": "STATE-BASIC", "active": True, "valid_until": "2027-06-30", "copay_waiver": False},
    "NG-0003": {"name": "Bola Adeyemi", "scheme": "STATE-BASIC", "active": False, "valid_until": "2026-01-31", "copay_waiver": False},
}
BENEFIT = {  # codes allowed per scheme family
    "BHCPF-VUL": ["MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB", "AMX-SUS", "ITN-001", "LAB-HB"],
    "STATE-BASIC": ["MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB", "AMX-SUS", "LAB-HB", "LAB-MP"],
}
TARIFF = {  # NGN per unit
    "MAL-RDT": 1500, "ANC-001": 4500, "ORS-001": 900, "PARA-TAB": 1200,
    "AMX-SUS": 2600, "ITN-001": 2100, "LAB-HB": 1800, "LAB-MP": 3500,
}
FREQ_CAP = {"MAL-RDT": 2, "PARA-TAB": 2}  # max units per member per 14 days
DUP_WINDOW_DAYS = 14
PRICE_DRIFT_TOLERANCE = 0.10


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def adjudicate(claim: dict) -> dict:
    reasons, flags = [], []
    member = MEMBERS.get(claim.get("member_id", ""))
    if not member:
        return {"claim_id": claim.get("claim_id"), "decision": "DENY",
                "reasons": ["ELIGIBILITY: unknown member"], "flags": [], "allowed_ngn": 0}
    if not member["active"] or _d(claim["service_date"]) > _d(member["valid_until"]):
        return {"claim_id": claim.get("claim_id"), "decision": "DENY",
                "reasons": ["ELIGIBILITY: coverage inactive or expired"], "flags": [], "allowed_ngn": 0}
    scheme = member["scheme"]
    allowed_codes = BENEFIT.get(scheme, [])
    total = 0
    for item in claim.get("items", []):
        code, qty = item["code"], int(item.get("qty", 1))
        if code not in allowed_codes:
            reasons.append(f"BENEFIT: {code} not covered under {scheme}")
            continue
        if code not in TARIFF:
            reasons.append(f"TARIFF: {code} missing from schedule")
            continue
        cap = FREQ_CAP.get(code)
        if cap and qty > cap:
            flags.append(f"FREQUENCY: {code} qty {qty} exceeds cap {cap} — pend for review")
        total += TARIFF[code] * qty
    # Duplicate detection: same member + provider + code within the window.
    window_start = _d(claim["service_date"])
    seen = {(h["member_id"], h["provider_id"], it["code"]) for h in claim.get("history", [])
            if 0 <= (window_start - _d(h["service_date"])).days <= DUP_WINDOW_DAYS
            for it in h.get("items", [])}
    for item in claim.get("items", []):
        if (claim["member_id"], claim["provider_id"], item["code"]) in seen:
            flags.append(f"DUPLICATE: {item['code']} dispensed to same member+provider within {DUP_WINDOW_DAYS} days")
    claimed = int(claim.get("claimed_ngn", 0))
    if claimed and total and abs(claimed - total) / total > PRICE_DRIFT_TOLERANCE:
        flags.append(f"PRICING: claimed NGN {claimed} vs tariff NGN {total} (>10% drift)")
    if total == 0 and any(r.startswith(("BENEFIT", "ELIGIBILITY")) for r in reasons):
        decision = "DENY"
    elif flags:
        decision = "PEND"
    else:
        decision = "APPROVE"
    if decision == "APPROVE" and not reasons:
        reasons = ["OK: eligible, in benefit, priced on tariff"]
    copay = 0 if member["copay_waiver"] else round(total * 0.10)
    return {"claim_id": claim.get("claim_id"), "member": member["name"], "scheme": scheme,
            "decision": decision, "reasons": reasons, "flags": flags,
            "allowed_ngn": total, "copay_ngn": copay, "payable_ngn": total - copay,
            "currency": "NGN", "adjudicated_by": "openhealth-adjudicator@0.2.0"}


if __name__ == "__main__":
    print(json.dumps(adjudicate(json.load(sys.stdin)), indent=2))
