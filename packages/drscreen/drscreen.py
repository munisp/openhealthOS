"""drscreen.py — diabetic retinopathy screening: fundus-to-referral.

The best-evidenced tele-screening pattern in medicine: camera at the
diabetes clinic, AI pre-grade lands as DRAFT under aigov governance,
a human grader confirms, referable disease becomes a tracked referral
(referred → attended → treated), because a screen nobody attends is a
rumour, not a programme.

Engine: teleread + aigov. This module owns the screening cascade.
"""

from teleread import DomainConfig, ReadingService, TeleReadError

EYES = ("left", "right", "both")
REFERRAL_GRADES = ("moderate-npdr", "severe-npdr", "pdr", "maculopathy")
CASCADE = ("screened", "referred", "attended", "treated")

CONFIG = DomainConfig(
    "dr-screening",
    scopes=("dr-grader",),
    critical_findings=("pdr", "maculopathy"),
    artifact_kinds=("fundus-image",),
    sla_hours={"stat": 24, "urgent": 72, "routine": 336})


class DRScreening:
    def __init__(self, readers, model_registry=None):
        self.reading = ReadingService(CONFIG, readers)
        self.models = model_registry
        self.cascade = {}   # patient_id -> {stage: day}

    def screen(self, patient_id, *, eye, image_ref, facility, referrer,
               day, ai_draft=None):
        if eye not in EYES:
            raise TeleReadError(f"unknown eye: {eye}")
        if not image_ref:
            raise TeleReadError("no fundus image — nothing to grade")
        case_id = f"dr-{patient_id}-{day}"
        if ai_draft is not None:
            if self.models is None:
                raise TeleReadError(
                    "AI pre-grade without a model registry is an "
                    "unauditable opinion — refused")
            self.models.submit_draft(case_id, **ai_draft)
        self.cascade.setdefault(patient_id, {})["screened"] = day
        return self.reading.order(
            case_id, priority="routine",
            question="diabetic retinopathy screening grade",
            facility=facility, referrer=referrer, day=day,
            artifact="fundus-image", meta={"patient_id": patient_id,
                                           "eye": eye})

    def record_grade(self, patient_id, *, grade, day):
        if grade in REFERRAL_GRADES:
            self.advance(patient_id, "referred", day=day)
        return {"patient_id": patient_id, "grade": grade,
                "referred": grade in REFERRAL_GRADES}

    def advance(self, patient_id, stage, *, day):
        c = self.cascade.get(patient_id)
        if not c:
            raise TeleReadError("unknown screening patient")
        idx = CASCADE.index(stage)
        done = [s for s in CASCADE if s in c]
        if len(done) != idx:
            raise TeleReadError(
                f"cascade is sequential: '{stage}' requires "
                f"'{CASCADE[idx - 1]}' first — attendance without "
                "referral is unverifiable")
        c[stage] = day
        return dict(c)

    def cascade_report(self):
        out = {s: 0 for s in CASCADE}
        for c in self.cascade.values():
            for s in CASCADE:
                out[s] += int(s in c)
        out["note"] = ("each stage is a subset of the previous; "
                       "attrition between referred and attended is the "
                       "number that matters")
        return out
