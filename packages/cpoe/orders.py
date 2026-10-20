"""CPOE — computerized provider order entry with order sets and a status
machine. Doctrine: allergy fail-CLOSED; interactions/dose warn-and-override
with mandatory reason capture (ADR-0056). Prescriber identity must be
credentialed (hrcred.check_prescriber) before an order can be placed.
"""
from __future__ import annotations
import json, sys, time
from formulary import Formulary, AllergyConflict

STATUS = ["draft", "proposed", "placed", "verified", "dispensed", "completed", "cancelled"]
TRANSITIONS = {
    "draft": {"proposed", "cancelled"},
    "proposed": {"placed", "cancelled", "draft"},
    "placed": {"verified", "cancelled"},
    "verified": {"dispensed", "cancelled"},
    "dispensed": {"completed"},
    "completed": set(), "cancelled": set(),
}

ORDER_SETS = {
    "malaria-uncomplicated": ["NG-ACT-AL", "NG-PARA500"],
    "uri-paediatric": ["NG-AMOX250S", "NG-PARA500"],
    "hypertension-start": ["NG-AMLO5"],
    "diabetes-type2-start": ["NG-METF500"],
    "anc-first-visit": ["NG-FEFOL", "NG-SP"],
    "pph-active-management": ["NG-OXYTOCIN"],
    "asthma-acute": ["NG-SALB100"],
}

class OrderError(Exception):
    pass
class TransitionError(OrderError):
    pass
class OverrideRequired(OrderError):
    """Major findings exist and no override reason was supplied."""

class Order:
    def __init__(self, oid, patient_id, prescriber_id, code, dose_mg, doses_per_day,
                 days, indication="", status="draft", findings=None, overrides=None):
        self.oid, self.patient_id, self.prescriber_id = oid, patient_id, prescriber_id
        self.code, self.dose_mg, self.doses_per_day, self.days = code, dose_mg, doses_per_day, days
        self.indication, self.status = indication, status
        self.findings = findings or []
        self.overrides = overrides or []  # [{"finding":..., "reason":..., "by":..., "at":...}]
        self.history = [("draft", int(time.time()))]

    def advance(self, to):
        if to not in TRANSITIONS[self.status]:
            raise TransitionError(f"{self.status} -> {to} not allowed")
        self.status = to
        self.history.append((to, int(time.time())))
        return self

    def to_dict(self):
        return {k: getattr(self, k) for k in
                ("oid","patient_id","prescriber_id","code","dose_mg","doses_per_day",
                 "days","indication","status","findings","overrides","history")}

class OrderService:
    def __init__(self, formulary=None, credential_check=None):
        self.fx = formulary or Formulary()
        # credential_check(prescriber_id) -> bool; default allow (tests),
        # production wires hrcred.check_prescriber (ADR-0056).
        self.credential_check = credential_check or (lambda pid: True)
        self.orders = {}

    def draft(self, oid, patient_id, prescriber_id, code, dose_mg, doses_per_day, days, indication=""):
        if not self.fx.lookup(code):
            raise OrderError(f"unknown drug code {code}")
        o = Order(oid, patient_id, prescriber_id, code, dose_mg, doses_per_day, days, indication)
        self.orders[oid] = o
        return o

    def draft_from_set(self, set_name, oid_prefix, patient_id, prescriber_id, days=7):
        if set_name not in ORDER_SETS:
            raise OrderError(f"unknown order set {set_name}")
        out = []
        for i, code in enumerate(ORDER_SETS[set_name]):
            d = self.fx.lookup(code)
            out.append(self.draft(f"{oid_prefix}-{i}", patient_id, prescriber_id, code,
                                  dose_mg=0, doses_per_day=0, days=days,
                                  indication=f"order-set:{set_name}"))
        return out

    def screen(self, oid, patient_allergies, active_codes):
        """Run safety screening. Allergy raises (fail-closed). Returns findings."""
        o = self.orders[oid]
        self.fx.check_allergy(o.code, patient_allergies)  # may raise AllergyConflict
        findings = self.fx.check_interactions(active_codes + [o.code])
        if o.dose_mg and o.doses_per_day:
            findings += self.fx.check_dose(o.code, o.dose_mg, o.doses_per_day)
        o.findings = findings
        return findings

    def place(self, oid, override_reasons=None):
        o = self.orders[oid]
        if not self.credential_check(o.prescriber_id):
            raise OrderError(f"prescriber {o.prescriber_id} not credentialed/licensed")
        major = [f for f in o.findings if f["severity"] in ("major", "contraindicated")]
        if major and not override_reasons:
            raise OverrideRequired(
                f"{len(major)} major finding(s) require override reason(s)")
        for r in (override_reasons or []):
            o.overrides.append({"finding": r["finding"], "reason": r["reason"],
                                "by": o.prescriber_id, "at": int(time.time())})
        if o.status == "draft":
            o.advance("proposed")
        return o.advance("placed")

def main(argv):
    fx = Formulary()
    cmd = argv[1] if len(argv) > 1 else ""
    doc = json.load(sys.stdin) if not sys.stdin.isatty() else {}
    if cmd == "screen":
        try:
            fx.check_allergy(doc["code"], doc.get("allergies"))
        except AllergyConflict as e:
            print(json.dumps({"ok": False, "block": str(e), "findings": []}))
            return 0
        findings = fx.check_interactions(doc.get("active", []) + [doc["code"]])
        if doc.get("dose_mg") and doc.get("doses_per_day"):
            findings += fx.check_dose(doc["code"], doc["dose_mg"], doc["doses_per_day"])
        print(json.dumps({"ok": True, "findings": findings}))
        return 0
    if cmd == "sets":
        print(json.dumps(ORDER_SETS))
        return 0
    if cmd == "lookup":
        print(json.dumps(fx.lookup(doc.get("code", "")) or {}))
        return 0
    print(json.dumps({"error": "unknown command"}))
    return 1

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
