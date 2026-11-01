"""pharmreview.py — remote clinical pharmacy review of high-risk orders.

The pharmacist is scarce; the dangerous prescription isn't. High-risk
order classes route to a credentialed pharmacist pool; interventions are
draft recommendations the prescriber accepts or overrides WITH A REASON
— an override without a reason is refused, because unaccountable
overrides are how errors hide.

Engine: teleread (the 'report' is a verification decision). This module
owns the order classes and the intervention loop.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

HIGH_RISK = ("anticoagulant", "insulin", "opioid", "nephrotoxic",
             "paediatric-weight-based", "chemotherapy", "aminoglycoside")

CONFIG = DomainConfig(
    "pharmacy-review",
    scopes=("clinical-pharmacist",),
    critical_findings=("contraindicated",),
    artifact_kinds=("prescription",),
    sla_hours={"stat": 1, "urgent": 8, "routine": 24})


class PharmacyReview:
    def __init__(self, readers):
        self.reading = ReadingService(CONFIG, readers)
        self.orders = {}

    def submit_order(self, case_id, *, patient_id, drug, dose, route,
                     risk_class, prescriber, facility, day,
                     priority="urgent"):
        if risk_class not in HIGH_RISK:
            raise TeleReadError(
                f"{risk_class} is not a high-risk class — routine orders "
                "do not queue for remote review")
        if not drug or dose is None:
            raise TeleReadError("drug and dose required")
        self.orders[case_id] = {
            "patient_id": patient_id, "drug": drug, "dose": dose,
            "route": route, "risk_class": risk_class,
            "prescriber": prescriber, "intervention": None}
        return self.reading.order(
            case_id, priority=priority,
            question=f"verify {drug} {dose} ({risk_class})",
            facility=facility, referrer=prescriber, day=day,
            artifact="prescription",
            meta={"patient_id": patient_id, "drug": drug,
                  "risk_class": risk_class})

    def intervene(self, case_id, *, pharmacist, day, recommendation,
                  reason):
        """Pharmacist's intervention is a draft TO the prescriber."""
        if case_id not in self.orders:
            raise TeleReadError(f"unknown order: {case_id}")
        if not recommendation or not reason:
            raise TeleReadError(
                "intervention requires a recommendation and a reason")
        self.orders[case_id]["intervention"] = {
            "by": pharmacist, "day": day, "recommendation": recommendation,
            "reason": reason, "prescriber_response": None}
        return dict(self.orders[case_id]["intervention"])

    def prescriber_responds(self, case_id, *, day, accept,
                            override_reason=None):
        o = self._order(case_id)
        if not o["intervention"]:
            raise TeleReadError("no intervention to respond to")
        if not accept and not override_reason:
            raise TeleReadError(
                "overriding a pharmacist requires a recorded reason — "
                "unaccountable overrides are how errors hide")
        o["intervention"]["prescriber_response"] = {
            "day": day, "accept": bool(accept),
            "override_reason": override_reason}
        return dict(o["intervention"])

    def acceptance_stats(self):
        n = sum(1 for o in self.orders.values() if o["intervention"])
        accepted = sum(1 for o in self.orders.values()
                       if o["intervention"]
                       and o["intervention"]["prescriber_response"]
                       and o["intervention"]["prescriber_response"]["accept"])
        return {"interventions": n, "accepted": accepted,
                "note": "acceptance = accepted/interventions; a very "
                        "low rate means the pool or the prescribers "
                        "need calibration, not silence"}

    def _order(self, cid):
        if cid not in self.orders:
            raise TeleReadError(f"unknown order: {cid}")
        return self.orders[cid]
