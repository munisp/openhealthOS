#!/usr/bin/env python3
"""OpenHealth OS — claims adjudication engine.

Reads a claim JSON on stdin, writes a decision JSON on stdout.
Deterministic and auditable: every rule that fires returns a reason or flag
code — adjudications are events, never silent verdicts.

Clean-room re-implementation of the adjudication pipeline pattern
(ADR-0005): eligibility → benefit → tariff → duplicate/fraud flags →
APPROVE / PEND / DENY. Member/benefit registries are in-file fixtures
here; production reads them from the FHIR/claims services over Dapr/Kafka.
The tariff schedule is NOT hard-coded: it loads from a versioned,
state-uploadable bundle (tariffs.py, ADR-0015) and every decision stamps
the bundle that priced it.
"""
import hashlib
import json
import os
import sys
import time
from datetime import datetime

# Sibling-module import that survives cross-package loading (replay,
# benchmark, and tools import this file by path, not as a package).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tariffs import active_bundle, tariff_map, freq_caps
from enrolment import load_members, enrol as enrol_member, suspend as suspend_member

# Fallback fixture for dev and tests. Production sets MEMBERS_FILE and
# the registry below is ignored — see get_member (ADR-0017).
MEMBERS = {
    "NG-0001": {"name": "Amina Yusuf", "scheme": "BHCPF-VUL", "active": True, "valid_until": "2027-12-31", "copay_waiver": True},
    "NG-0002": {"name": "Chinedu Okafor", "scheme": "STATE-BASIC", "active": True, "valid_until": "2027-06-30", "copay_waiver": False},
    "NG-0003": {"name": "Bola Adeyemi", "scheme": "STATE-BASIC", "active": False, "valid_until": "2026-01-31", "copay_waiver": False},
}
BENEFIT = {  # codes allowed per scheme family
    "BHCPF-VUL": ["MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB", "AMX-SUS", "ITN-001", "LAB-HB"],
    "STATE-BASIC": ["MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB", "AMX-SUS", "LAB-HB", "LAB-MP"],
}

# Tariff bundle in force at import time (ADR-0015). Per-call reload lives
# in _schedule() so a state upload takes effect without a redeploy.
_BUNDLE = active_bundle()
TARIFF = tariff_map(_BUNDLE)          # kept as a module attr: importers relied on it
FREQ_CAP = freq_caps(_BUNDLE)         # max units per member per 14 days
TARIFF_STAMP = f"{_BUNDLE['bundle_id']}@v{_BUNDLE['version']}"
DUP_WINDOW_DAYS = 14
PRICE_DRIFT_TOLERANCE = 0.10

# Member registry seam (ADR-0017): MEMBERS_FILE points at the durable
# enrolment store; unset means the dev fixture above. In-process
# importers (benchmark, replay) cache with a stat signature AND a short
# TTL — filesystem timestamp granularity varies (overlayfs, network
# mounts), so correctness never rests on mtime alone: worst case the
# cache is 1s stale, and the production path (subprocess per check)
# always reads fresh. A state enrolment sync takes effect without a
# redeploy, exactly like tariff bundles.
_MEMBERS_CACHE_TTL_S = 1.0
_members_cache: tuple[tuple, float, dict] | None = None


def get_member(member_id: str) -> dict | None:
    global _members_cache
    path = os.environ.get("MEMBERS_FILE", "")
    if not path:
        return MEMBERS.get(member_id)
    try:
        st = os.stat(path)
        sig = (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        sig = (-1, -1, -1)
    now = time.monotonic()
    if (_members_cache is None or _members_cache[0] != sig
            or now - _members_cache[1] > _MEMBERS_CACHE_TTL_S):
        _members_cache = (sig, now, load_members(path))
    return _members_cache[2].get(member_id)


def _schedule() -> tuple[dict[str, int], dict[str, int], str]:
    """Reload the active bundle each call (state uploads take effect live);
    fall back to the import-time bundle if the override vanishes."""
    try:
        b = active_bundle()
        return tariff_map(b), freq_caps(b), f"{b['bundle_id']}@v{b['version']}"
    except Exception:
        return TARIFF, FREQ_CAP, TARIFF_STAMP

# Pre-authorisation (claims-gateway /preauth/request). Lines at or above
# this estimated cost always require a clinical note; without one the
# request pends for medical review instead of failing open.
PREAUTH_NOTE_THRESHOLD_NGN = 10000


def _d(s):
    return datetime.strptime(s, "%Y-%m-%d").date()


def adjudicate(claim: dict) -> dict:
    tariff, freq_cap, stamp = _schedule()
    reasons, flags = [], []
    member = get_member(claim.get("member_id", ""))
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
        if code not in tariff:
            reasons.append(f"TARIFF: {code} missing from schedule")
            continue
        cap = freq_cap.get(code)
        if cap and qty > cap:
            flags.append(f"FREQUENCY: {code} qty {qty} exceeds cap {cap} — pend for review")
        total += tariff[code] * qty
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
    if decision == "PEND" and not reasons:
        # A pended verdict must state why in reasons, not only in flags —
        # silent verdicts are the failure mode this engine exists to kill.
        reasons = [f"REVIEW: {f}" for f in flags]
    copay = 0 if member["copay_waiver"] else round(total * 0.10)
    return {"claim_id": claim.get("claim_id"), "member": member["name"], "scheme": scheme,
            "decision": decision, "reasons": reasons, "flags": flags,
            "allowed_ngn": total, "copay_ngn": copay, "payable_ngn": total - copay,
            "currency": "NGN", "tariff_bundle": stamp,
            "adjudicated_by": "openhealth-adjudicator@0.2.1"}


def check_eligibility(member_id: str, service_date: str) -> dict:
    """Real-time eligibility verification (claims-gateway /eligibility/check).
    The same verdict the USSD fallback channel returns — one seam, one answer.

    ADR-0017: the answer now says WHY when the answer is no (suspended vs
    expired vs unknown — each has a different remedy at the desk), carries
    the benefit package size so the point of care can talk plainly ("your
    plan covers 7 services here"), and surfaces biometric_required for
    schemes that demand a match before high-value care (anti card-sharing).
    """
    member = get_member(member_id)
    if not member:
        return {"member_id": member_id, "eligible": False,
                "reason": "unknown member — visit your PHC to enrol", "source": "live"}
    scheme = member["scheme"]
    benefit = BENEFIT.get(scheme, [])
    out = {"member_id": member_id, "scheme": scheme,
           "valid_until": member["valid_until"],
           "copay_waiver": member.get("copay_waiver", False),
           "biometric_required": member.get("biometric_required", False),
           "benefit_services": len(benefit), "source": "live"}
    if not member["active"]:
        reason = member.get("suspend_reason") or "coverage suspended"
        return {**out, "eligible": False, "reason": f"coverage inactive — {reason}"}
    if _d(service_date) > _d(member["valid_until"]):
        return {**out, "eligible": False,
                "reason": f"coverage expired {member['valid_until']} — renew at your PHC"}
    return {**out, "eligible": True, "reason": "active"}


def preauth(request: dict) -> dict:
    """Pre-authorisation decision for tariff lines (claims-gateway
    /preauth/request). Same eligibility and benefit rules as adjudication
    — a preauth that approves what adjudication would deny is a promise
    the scheme cannot keep. The preauth id is deterministic: the same
    request always yields the same id, so offline replays dedupe."""
    tariff, _caps, stamp = _schedule()
    reasons = []
    member = get_member(request.get("member_id", ""))
    if not member:
        return {"preauth_id": None, "status": "DENIED",
                "reasons": ["ELIGIBILITY: unknown member"], "estimated_ngn": 0}
    if not member["active"]:
        return {"preauth_id": None, "status": "DENIED",
                "reasons": ["ELIGIBILITY: coverage inactive"], "estimated_ngn": 0}
    scheme = member["scheme"]
    allowed_codes = BENEFIT.get(scheme, [])
    total, denied_lines = 0, []
    for item in request.get("items", []):
        code, qty = item["code"], int(item.get("qty", 1))
        if code not in allowed_codes:
            denied_lines.append(code)
            reasons.append(f"BENEFIT: {code} not covered under {scheme}")
        elif code not in tariff:
            denied_lines.append(code)
            reasons.append(f"TARIFF: {code} missing from schedule")
        else:
            total += tariff[code] * qty
    if denied_lines and total == 0:
        return {"preauth_id": None, "status": "DENIED", "reasons": reasons,
                "estimated_ngn": 0}
    canonical = "|".join([
        request.get("member_id", ""), request.get("provider_id", ""),
        ",".join(f"{i['code']}x{int(i.get('qty', 1))}"
                 for i in sorted(request.get("items", []), key=lambda i: i["code"])),
    ])
    preauth_id = "PA-" + hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()
    if total >= PREAUTH_NOTE_THRESHOLD_NGN and not request.get("clinical_note"):
        reasons.append(f"REVIEW: estimated NGN {total} ≥ {PREAUTH_NOTE_THRESHOLD_NGN} "
                       "requires a clinical note — pended for medical review")
        return {"preauth_id": preauth_id, "status": "PENDED", "reasons": reasons,
                "estimated_ngn": total}
    if denied_lines:
        reasons.append("PARTIAL: uncovered lines excluded from authorisation")
    if not reasons:
        reasons = ["OK: eligible, in benefit, priced on tariff"]
    return {"preauth_id": preauth_id, "status": "APPROVED", "reasons": reasons,
            "estimated_ngn": total, "scheme": scheme, "tariff_bundle": stamp,
            "adjudicated_by": "openhealth-adjudicator@0.3.1"}


if __name__ == "__main__":
    if "--check" in sys.argv:  # eligibility mode: stdin {"member_id","service_date"}
        req = json.load(sys.stdin)
        print(json.dumps(check_eligibility(req["member_id"], req.get("service_date", "2026-10-17"))))
    elif "--enrol" in sys.argv:  # enrolment mode (ADR-0017): stdin member record
        print(json.dumps(enrol_member(json.load(sys.stdin)), indent=2))
    elif "--suspend" in sys.argv:  # suspend mode: stdin {"member_id","reason"}
        req = json.load(sys.stdin)
        print(json.dumps(suspend_member(req["member_id"], req.get("reason", "")), indent=2))
    elif "--preauth" in sys.argv:  # preauth mode: stdin {"member_id","provider_id","items":[...]}
        print(json.dumps(preauth(json.load(sys.stdin)), indent=2))
    else:
        print(json.dumps(adjudicate(json.load(sys.stdin)), indent=2))
