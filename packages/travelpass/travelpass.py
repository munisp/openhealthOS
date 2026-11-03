"""travelpass.py — traveler medical insurance: the border collects the
fee, the QR opens the record, the emergency never waits for the
invoice.

A country mandates a medical-insurance fee for every inbound tourist.
This package is the whole loop:

  1. ENROLL at/before entry: identity, coverage window (entry→exit
     dates), and the EMERGENCY PROFILE — the subset of the medical
     record an ER needs in the first ten minutes (blood group,
     allergies, conditions, medications, emergency contacts). Absence
     is stated as not-recorded, never blanked into looking empty.
  2. PAY: the premium is assessed from a versioned policy table
     (flat fee + per-day), collected into an append-only ledger with
     receipts. A partial payment is a stated shortfall, not a silent
     discount. Coverage status is a fact: active / shortfall /
     expired.
  3. QR PASS: the QR carries a TOKEN, never the record — the code is
     a key, not the data. Tokens are random, hashed at rest,
     revocable, single-scope.
  4. BREAK-GLASS: an emergency provider scans the token, names
     themselves, their facility, and the reason — and gets the
     emergency subset. Coverage status gates the invoice, NEVER the
     airway: a lapsed policy still opens the record, loudly flagged.
     Every access is an append-only audit entry with a session id.
  5. CLAIMS: the emergency event binds to the access session; active
     coverage at event time pays, lapsed coverage flags recovery —
     the money follows the event, not the other way round.

Doctrine: fail-closed, append-only, denominators everywhere, and the
border rule — a traveler with no record is not a traveler with no
history; they are a traveler the system has not met.
"""

import hashlib
import secrets

TRAVEL_POLICY_VERSION = "travel-medical-policy@1.0.0"
TRAVELPASS_VERSION = "travelpass@1.0.0"

# Versioned premium policy — a fee that cannot be traced to a table
# is a bribe with a receipt.
POLICY = {
    "flat_fee_kobo": 500_000,        # ₦5,000 entry fee
    "per_day_kobo": 50_000,          # ₦500 per covered day
    "max_covered_days": 90,          # beyond this is residence, not tourism
}
PAYMENT_METHODS = ("card", "transfer", "cash", "wallet")
EMERGENCY_FIELDS = ("blood_group", "allergies", "conditions",
                    "medications", "emergency_contacts")
BLOOD_GROUPS = ("A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
ACCESS_TTL_DAYS = 1  # an emergency session lives for the emergency


class TravelPassError(Exception):
    pass


def quote_premium(entry_day, exit_day):
    """The fee from the table. Windows beyond the tourism cap refuse —
    a 2-year 'tourist' needs a different product, not a loophole."""
    if exit_day < entry_day:
        raise TravelPassError("exit before entry — time does not run "
                              "backwards at the border")
    days = exit_day - entry_day + 1
    if days > POLICY["max_covered_days"]:
        raise TravelPassError(
            f"{days}-day stay exceeds the {POLICY['max_covered_days']}-"
            "day tourist cap — this traveller needs a resident policy, "
            "not a bigger tourist one")
    return {"days": days,
            "premium_kobo": (POLICY["flat_fee_kobo"]
                             + POLICY["per_day_kobo"] * days),
            "policy_version": TRAVEL_POLICY_VERSION}


class TravelPassRegistry:
    def __init__(self, country_code):
        if not country_code:
            raise TravelPassError("the issuing authority is part of "
                                  "every token's provenance")
        self.country = country_code
        self.travelers = {}     # traveler_id -> record
        self.payments = []      # append-only ledger
        self.tokens = {}        # token_hash -> traveler_id
        self.revoked = set()    # token_hash
        self.access_log = []    # append-only break-glass audit
        self.emergencies = []   # append-only events
        self._rcp = 0
        self._er = 0

    # ---- 1. enrollment ----
    def enroll(self, traveler_id, *, full_name, passport_ref,
               nationality, entry_day, exit_day, emergency_profile,
               day):
        if traveler_id in self.travelers:
            raise TravelPassError(f"duplicate enrollment: {traveler_id}")
        if not full_name or not passport_ref:
            raise TravelPassError("name and passport reference are the "
                                  "record — a traveler without identity "
                                  "is a ticket number")
        q = quote_premium(entry_day, exit_day)
        profile = dict(emergency_profile or {})
        bg = profile.get("blood_group")
        if bg is not None and bg not in BLOOD_GROUPS:
            raise TravelPassError(f"unknown blood group: {bg} — "
                                  "unbound is not a code")
        for field in ("allergies", "conditions", "medications",
                      "emergency_contacts"):
            profile.setdefault(field, [])
        profile.setdefault("blood_group", None)  # absence stays absent
        self.travelers[traveler_id] = {
            "traveler_id": traveler_id, "full_name": full_name,
            "passport_ref": passport_ref, "nationality": nationality,
            "entry_day": entry_day, "exit_day": exit_day,
            "emergency_profile": profile,
            "assessed_premium_kobo": q["premium_kobo"],
            "policy_version": q["policy_version"],
            "enrolled_day": day, "revoked": False,
        }
        return dict(self.travelers[traveler_id])

    # ---- 2. payments: append-only ledger ----
    def pay(self, traveler_id, *, amount_kobo, method, reference, day):
        t = self._t(traveler_id)
        if method not in PAYMENT_METHODS:
            raise TravelPassError(f"unknown payment method: {method}")
        if amount_kobo <= 0:
            raise TravelPassError("a zero payment is not a payment")
        if not reference:
            raise TravelPassError("a payment without a reference is "
                                  "cash in a pocket, not a receipt")
        if any(p["reference"] == reference for p in self.payments):
            raise TravelPassError(f"duplicate reference {reference} — "
                                  "reused references are how fees get "
                                  "collected twice and recorded once")
        self._rcp += 1
        entry = {"receipt": f"RCP-{self._rcp}",
                 "traveler_id": traveler_id, "amount_kobo": amount_kobo,
                 "method": method, "reference": reference, "day": day}
        self.payments.append(entry)
        paid = self.paid_kobo(traveler_id)
        entry["coverage"] = self.coverage_status(traveler_id,
                                                 day)["status"]
        entry["shortfall_kobo"] = max(
            0, t["assessed_premium_kobo"] - paid)
        return dict(entry)

    def paid_kobo(self, traveler_id):
        return sum(p["amount_kobo"] for p in self.payments
                   if p["traveler_id"] == traveler_id)

    def coverage_status(self, traveler_id, day):
        t = self._t(traveler_id)
        paid = self.paid_kobo(traveler_id)
        due = t["assessed_premium_kobo"]
        if not (t["entry_day"] <= day <= t["exit_day"]):
            return {"status": "expired", "paid_kobo": paid,
                    "due_kobo": due,
                    "note": "outside the coverage window"}
        if paid >= due:
            return {"status": "active", "paid_kobo": paid,
                    "due_kobo": due}
        return {"status": "shortfall", "paid_kobo": paid,
                "due_kobo": due, "shortfall_kobo": due - paid,
                "note": "partial payment is a stated shortfall, not "
                        "a silent discount"}

    # ---- 3. QR pass: the code is a key, not the record ----
    def issue_pass(self, traveler_id, *, day):
        t = self._t(traveler_id)
        if t["revoked"]:
            raise TravelPassError("traveler record revoked")
        token = f"tp_{secrets.token_urlsafe(24)}"
        h = hashlib.sha256(token.encode()).hexdigest()
        self.tokens[h] = traveler_id
        return {"traveler_id": traveler_id, "token": token,
                "qr_payload": f"https://er.{self.country}.gov/er#t={token}",
                "issued_day": day,
                "note": "the QR carries a key, never the record; "
                        "the hash is what we store"}

    def revoke_pass(self, token, *, day, by, reason):
        h = hashlib.sha256(token.encode()).hexdigest()
        if h not in self.tokens:
            raise TravelPassError("unknown token — revoking a ghost is "
                                  "paperwork")
        if not reason or not by:
            raise TravelPassError("revocation without a reason and a "
                                  "name is a silent deletion")
        self.revoked.add(h)
        return {"revoked": True, "day": day, "by": by}

    # ---- 4. break-glass emergency access ----
    def emergency_access(self, token, *, provider_id, facility, reason,
                         day):
        """Coverage gates the invoice, never the airway: the record
        opens for active, shortfall AND expired coverage — but the
        coverage state rides on the response, loudly. Only the
        emergency subset is ever returned."""
        h = hashlib.sha256(token.encode()).hexdigest()
        if h not in self.tokens:
            raise TravelPassError("unknown token — a QR that resolves "
                                  "to nothing is stated as nothing; "
                                  "treat the patient in front of you")
        if h in self.revoked:
            raise TravelPassError("token revoked — the record stays "
                                  "closed; revocation is a clinical-"
                                  "security event, not a suggestion")
        if not provider_id or not facility or not reason:
            raise TravelPassError("break-glass without WHO, WHERE and "
                                  "WHY is not access, it is a breach")
        traveler_id = self.tokens[h]
        t = self.travelers[traveler_id]
        coverage = self.coverage_status(traveler_id, day)
        self._er += 1
        session = {"session_id": f"ER-{self._er}",
                   "traveler_id": traveler_id, "day": day,
                   "provider_id": provider_id, "facility": facility,
                   "reason": reason,
                   "coverage_at_access": coverage["status"],
                   "ttl_days": ACCESS_TTL_DAYS}
        self.access_log.append(session)
        profile = t["emergency_profile"]
        card = {"session_id": session["session_id"],
                "full_name": t["full_name"],
                "nationality": t["nationality"],
                "blood_group": (profile["blood_group"]
                                if profile["blood_group"] is not None
                                else "not-recorded"),
                "allergies": profile["allergies"] or "none-recorded",
                "conditions": profile["conditions"] or "none-recorded",
                "medications": profile["medications"] or "none-recorded",
                "emergency_contacts": profile["emergency_contacts"]
                or "none-recorded",
                "coverage": coverage,
                "note": "emergency subset only — the full record is "
                        "not on this card; absence is stated, never "
                        "blanked"}
        return card

    # ---- 5. the emergency event binds the claim ----
    def record_emergency(self, traveler_id, *, session_id, day,
                         facility, summary):
        self._t(traveler_id)
        session = next((s for s in self.access_log
                        if s["session_id"] == session_id), None)
        if session is None:
            raise TravelPassError("an emergency event binds to an "
                                  "access session — care without the "
                                  "audit is a claim without a leg")
        if session["traveler_id"] != traveler_id:
            raise TravelPassError("session belongs to another traveler "
                                  "— crossed records are the finding "
                                  "this check exists to catch")
        if not summary:
            raise TravelPassError("an emergency without a summary is "
                                  "an invoice without a diagnosis")
        payable = session["coverage_at_access"] == "active"
        event = {"traveler_id": traveler_id, "session_id": session_id,
                 "day": day, "facility": facility, "summary": summary,
                 "payable": payable,
                 "note": ("coverage active at access — claim payable"
                          if payable else
                          "coverage lapsed/shortfall at access — care "
                          "given, recovery flagged; the invoice never "
                          "gated the airway")}
        self.emergencies.append(event)
        return dict(event)

    # ---- 6. reports ----
    def report(self, as_of):
        total = len(self.travelers)
        statuses = {"active": 0, "shortfall": 0, "expired": 0}
        for tid in self.travelers:
            statuses[self.coverage_status(tid, as_of)["status"]] += 1
        return {"travelers_enrolled": total,
                "coverage": statuses,
                "premium_collected_kobo": sum(p["amount_kobo"]
                                              for p in self.payments),
                "emergency_accesses": len(self.access_log),
                "emergencies": len(self.emergencies),
                "payable": sum(1 for e in self.emergencies
                               if e["payable"]),
                "version": TRAVELPASS_VERSION,
                "note": "every count visible; premium is summed from "
                        "the ledger, never typed in"}

    def _t(self, traveler_id):
        if traveler_id not in self.travelers:
            raise TravelPassError(f"unknown traveler: {traveler_id} — "
                                  "the system has not met them; that "
                                  "is stated, not zeroed")
        return self.travelers[traveler_id]


# ----------------------------------------------------------------
# The three questions every border deployment asks:
#   1. Does the enrollment reach the EMR?  → YES, two ways below.
#   2. Does it reach the traveler/provider on their channels?
#      → the chat/notification seams below.
#   3. Where does the money go?  → the fee split + settlement below.

import json as _json
import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..",
                                  "ips"))
from ips import build_ips  # noqa: E402

FEE_SPLIT_VERSION = "fee-split@1.0.0"
# The premium is two monies, never one: the administration fee pays
# for running the scheme (border desks, the registry, the QR
# infrastructure); the risk pool pays claims. Conflating them is how
# schemes quietly spend the claims money on administration.
FEE_SPLIT = {"admin_pct": 15, "pool_pct": 85}
NOTIFY_KINDS = ("pass-issued", "payment-receipt", "coverage-expiring",
                "emergency-accessed", "refund-processed")


# ---- 1. the EMR link ----------------------------------------------

def ips_document(registry, traveler_id, *, author, day):
    """FHIR R4 International Patient Summary from the emergency
    profile — the format any conformant EMR on earth can import. The
    traveler arrives unconscious at a foreign ED; the IPS is the
    standard for exactly that room. Self-declared-at-enrollment lists
    that came back empty are stated 'none-known' — the traveler was
    asked and said none; that is a different fact from never asking."""
    t = registry._t(traveler_id)
    p = t["emergency_profile"]

    def section(items):
        return ({"entries": items} if items
                else {"empty_reason": "none-known"})

    bundle = build_ips(
        {"id": traveler_id, "name": t["full_name"],
         "country": t["nationality"]},
        sections={
            "medications": section([{"display": m}
                                    for m in p["medications"]]),
            "allergies": section([{"display": a}
                                  for a in p["allergies"]]),
            "problems": section([{"display": c}
                                 for c in p["conditions"]]),
            "vitals": {"empty_reason": "not-collected"},
        },
        author=author, generated_day=day)
    bundle["x-travelpass"] = {"coverage_window":
                              [t["entry_day"], t["exit_day"]],
                              "policy_version": t["policy_version"]}
    return bundle


def emr_encounter(registry, traveler_id, session_id, *, day,
                  sink=None):
    """Break-glass opens an encounter in the receiving facility's EMR,
    linked to the traveler record. sink is duck-typed
    (callable(traveler_ref, encounter) -> emr_ref): with a sink the
    link is real; without one the encounter is UNLINKED — stated
    loudly, because care never waits for integration but the audit
    never pretends it happened."""
    session = next((s for s in registry.access_log
                    if s["session_id"] == session_id), None)
    if session is None:
        raise TravelPassError("no such access session — an encounter "
                              "without break-glass provenance is a "
                              "record without a door")
    if session["traveler_id"] != traveler_id:
        raise TravelPassError("session belongs to another traveler")
    t = registry._t(traveler_id)
    encounter = {"traveler_id": traveler_id, "session_id": session_id,
                 "facility": session["facility"], "day": day,
                 "identity": {"passport_ref": t["passport_ref"],
                              "full_name": t["full_name"]},
                 "coverage_at_access": session["coverage_at_access"]}
    if sink is None:
        encounter["emr_link"] = "unlinked"
        encounter["note"] = ("no EMR sink wired at this facility — the "
                             "encounter is recorded here, the link is "
                             "an open integration task, stated not "
                             "silent")
    else:
        encounter["emr_link"] = "linked"
        encounter["emr_ref"] = sink(
            {"traveler_id": traveler_id,
             "passport_ref": t["passport_ref"]}, encounter)
    return encounter


# ---- 2. the channels ----------------------------------------------

def notification_line(registry, kind, traveler_id, *, day, content_ref):
    """An NDJSON line for the howUdey chat seam — the same envelope
    the radiology report-ready seam emits. The rail carries POINTERS,
    never PHI: content_ref names the artifact (the pass, the receipt,
    the coverage state); the medical record itself never rides the
    chat rail. WhatsApp delivery composes through resultloop's
    ChannelAdapter — no rail configured means honestly queued, never
    claimed-sent."""
    if kind not in NOTIFY_KINDS:
        raise TravelPassError(f"unknown notification kind: {kind}")
    registry._t(traveler_id)
    if not content_ref:
        raise TravelPassError("a notification without a content "
                              "reference is a rumour — the rail "
                              "carries pointers, not payloads")
    return _json.dumps({"kind": kind, "traveler_id": traveler_id,
                        "content_ref": content_ref, "day": day},
                       sort_keys=True)


def expiring_passes(registry, as_of, within_days=3):
    """Coverage expiring soon — the traveler hears it before the
    border does. Coverage lapses are a notification problem before
    they are a claims problem."""
    out = []
    for tid, t in registry.travelers.items():
        days_left = t["exit_day"] - as_of
        if t["entry_day"] <= as_of <= t["exit_day"] and \
                days_left <= within_days:
            out.append({"traveler_id": tid, "days_left": days_left,
                        "coverage": registry.coverage_status(
                            tid, as_of)["status"]})
    out.sort(key=lambda x: x["days_left"])
    return out


# ---- 3. the money --------------------------------------------------

def settle(registry, router, *, day, rail="", pool_account,
           admin_account):
    """Settle collected premiums into their two homes over the payment
    rails. One tx_id settles on exactly one rail (the router refuses
    cross-rail replay); a settlement never runs twice over the same
    receipts; without a router the settlement refuses — the money is
    collected, the settlement is not pretend."""
    unsettled = [p for p in registry.payments
                 if not p.get("settled") and p["amount_kobo"] > 0]
    if not unsettled:
        raise TravelPassError("nothing unsettled — settling zero is a "
                              "ledger entry that exists to confuse "
                              "auditors")
    if router is None:
        raise TravelPassError("no payment rail — the settlement "
                              "refuses to be imaginary; wire payrails "
                              "(Mojaloop / NIBSS)")
    total = sum(p["amount_kobo"] for p in unsettled)
    admin = total * FEE_SPLIT["admin_pct"] // 100
    pool = total - admin
    registry.settlements = getattr(registry, "settlements", [])
    seq = len(registry.settlements) + 1
    results = {}
    for leg, amount, account in (("admin", admin, admin_account),
                                 ("pool", pool, pool_account)):
        res = router.transfer(
            _settle_request(f"TP-SETTLE-{seq}-{leg}", amount, account),
            rail=rail or "")
        if res.status != "COMPLETED":
            raise TravelPassError(
                f"{leg} tranche failed to settle ({res.status}: "
                f"{res.detail}) — the batch stays unsettled; "
                "a half-settled scheme is a finding, not a state")
        results[leg] = {"amount_kobo": amount,
                        "reference": res.reference, "rail": res.rail}
    for p in unsettled:
        p["settled"] = {"day": day,
                        "settlement": f"TP-SETTLE-{seq}"}
    entry = {"settlement_id": f"TP-SETTLE-{seq}", "day": day,
             "receipts": len(unsettled), "gross_kobo": total,
             "admin_kobo": admin, "pool_kobo": pool,
             "split": dict(FEE_SPLIT), "version": FEE_SPLIT_VERSION,
             "rails": results}
    registry.payments.append({  # tombstone row, append-only
        "receipt": f"TP-SETTLE-{seq}", "traveler_id": None,
        "amount_kobo": 0, "method": "settlement", "reference":
        f"TP-SETTLE-{seq}", "day": day, "settled": True})
    registry.settlements.append(entry)
    return entry


def _settle_request(tx_id, amount_kobo, account):
    """Build a payrails PaymentRequest by duck-typing (payrails is a
    sibling package; the import is local so travelpass stays loadable
    standalone)."""
    _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__),
                                      "..", "payrails"))
    from payrails import PaymentRequest
    return PaymentRequest(tx_id=tx_id, payee=account,
                          amount_ngn=amount_kobo // 100,
                          narration="travelpass settlement",
                          bank_code="", account=account)


def refund_early_exit(registry, traveler_id, *, actual_exit_day, day,
                      reason):
    """Pro-rata refund of the UNUSED per-day portion when a traveler
    leaves early. The flat fee and the administration share never
    refund — the scheme was administered, the desk was staffed. A
    traveler with an emergency event refunds nothing: the pool stood
    ready, that readiness was the product. Append-only negative
    ledger entry; the original receipts never rewrite."""
    t = registry._t(traveler_id)
    if not reason:
        raise TravelPassError("a refund without a reason is money "
                              "leaving without a story")
    if not (t["entry_day"] <= actual_exit_day <= t["exit_day"]):
        raise TravelPassError("actual exit outside the coverage "
                              "window — that is not early exit")
    if any(e["traveler_id"] == traveler_id
           for e in registry.emergencies):
        raise TravelPassError("emergency event on record — the pool "
                              "stood ready; readiness was the product, "
                              "nothing refunds")
    if any(p["traveler_id"] == traveler_id and p["amount_kobo"] < 0
           for p in registry.payments):
        raise TravelPassError("already refunded — the second refund is "
                              "the fraud this ledger exists to catch")
    unused_days = t["exit_day"] - actual_exit_day
    refundable = (POLICY["per_day_kobo"] * unused_days
                  * FEE_SPLIT["pool_pct"] // 100)
    registry._rcp += 1
    entry = {"receipt": f"RCP-{registry._rcp}",
             "traveler_id": traveler_id,
             "amount_kobo": -refundable,
             "method": "refund", "reference": f"refund-{traveler_id}",
             "day": day, "reason": reason,
             "note": "pro-rata pool share of unused days; flat fee "
                     "and admin share never refund"}
    registry.payments.append(entry)
    return dict(entry)
