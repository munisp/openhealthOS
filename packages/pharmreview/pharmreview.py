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
from drugkb import check_order

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
                     priority="urgent", dose_mg_per_day=None,
                     current_meds=(), allergies=(), egfr=None):
        if risk_class not in HIGH_RISK:
            raise TeleReadError(
                f"{risk_class} is not a high-risk class — routine orders "
                "do not queue for remote review")
        if not drug or dose is None:
            raise TeleReadError("drug and dose required")
        # the knowledge base meets the order BEFORE it queues:
        # interactions, duplication, allergy, dose ceiling, renal.
        # A contraindicated finding escalates the review to STAT —
        # the meeting is automatic and loud, never a quiet queue line.
        safety = check_order(drug, dose_mg_per_day=dose_mg_per_day,
                             current_meds=current_meds,
                             allergies=allergies, egfr=egfr)
        contra = [f for f in safety["findings"]
                  if f["severity"] == "contraindicated"]
        if contra:
            priority = "stat"
        self.orders[case_id] = {
            "patient_id": patient_id, "drug": drug, "dose": dose,
            "route": route, "risk_class": risk_class,
            "prescriber": prescriber, "intervention": None,
            "safety": safety}
        return self.reading.order(
            case_id, priority=priority,
            question=f"verify {drug} {dose} ({risk_class})",
            facility=facility, referrer=prescriber, day=day,
            artifact="prescription",
            meta={"patient_id": patient_id, "drug": drug,
                  "risk_class": risk_class,
                  "safety_findings": len(safety["findings"]),
                  "contraindicated": bool(contra)})

    def queue_timeout(self, course, *, day):
        """A stewardship antibiotic timeout lands in the pharmacist
        queue. Idempotent per due-day: a continued course that comes
        due again queues a NEW review (due_day is in the case id)."""
        cid = f"timeout-{course['course_id']}-due{course['due_day']}"
        if cid in self.orders:
            return {"case_id": cid, "already_queued": True}
        self.orders[cid] = {
            "patient_id": course["patient_id"],
            "drug": course["drug_code"], "dose": "timeout-review",
            "route": course["route"], "risk_class": "antibiotic-timeout",
            "prescriber": course["prescriber"], "intervention": None,
            "timeout": dict(course)}
        out = self.reading.order(
            cid, priority="urgent",
            question=(f"antibiotic timeout: {course['drug_code']} "
                      f"{course['overdue_days']}d past the 48h review"),
            facility="timeout-clock", referrer=course["prescriber"],
            day=day, artifact="prescription",
            meta={"patient_id": course["patient_id"],
                  "drug": course["drug_code"],
                  "aware": course["aware"]})
        return dict(out, already_queued=False)

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
