"""drscreen.py — diabetic retinopathy screening: fundus-to-referral.

The best-evidenced tele-screening pattern in medicine: camera at the
diabetes clinic, AI pre-grade lands as DRAFT under aigov governance,
a human grader confirms, referable disease becomes a tracked referral
(referred → attended → treated), because a screen nobody attends is a
rumour, not a programme.

Two credibility gates now stand in front of the cascade (gap #8):
  - capture-time QA: no gradable QA record, no screen. An ungradable
    image is a failed screen, not a negative.
  - two-grader arbitration: one grade is an opinion; agreement is
    final; disagreement goes to a senior third pair of eyes. No
    referral fires on a disputed grade — but a disputed CRITICAL grade
    is loud, not silent.

Engine: teleread + aigov + duelgrade. This module owns the cascade.
"""

from teleread import DomainConfig, ReadingService, TeleReadError
from duelgrade import CaptureQA, DuelGrader, DuelError, recall_plan

EYES = ("left", "right", "both")
REFERRAL_GRADES = ("moderate-npdr", "severe-npdr", "pdr", "maculopathy")
DR_GRADES = ("no-dr", "mild-npdr", "moderate-npdr", "severe-npdr",
             "pdr", "maculopathy")
CASCADE = ("screened", "referred", "attended", "treated")

FUNDUS_CHECKS = ("field-definition", "focus", "illumination",
                 "media-clarity")

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
        self.qa = CaptureQA("fundus", FUNDUS_CHECKS)
        self.grading = DuelGrader(readers, scope="dr-grader",
                                  grades=DR_GRADES,
                                  model_registry=model_registry)
        self.cascade = {}        # patient_id -> {stage: day}
        self.patient_cases = {}  # patient_id -> [case_id]
        self.recall = {}         # patient_id -> surveillance plan

    def assess_capture(self, image_ref, *, day, by, passed=(), failed=()):
        return self.qa.assess(image_ref, day=day, by=by,
                              passed=passed, failed=failed)

    def screen(self, patient_id, *, eye, image_ref, facility, referrer,
               day, ai_draft=None):
        if eye not in EYES:
            raise TeleReadError(f"unknown eye: {eye}")
        if not image_ref:
            raise TeleReadError("no fundus image — nothing to grade")
        if not self.qa.gradable(image_ref):
            raise TeleReadError(
                "no gradable capture QA on record — an unassessed image "
                "is not a screen, and an ungradable image is a FAILED "
                "screen, not a negative")
        case_id = f"dr-{patient_id}-{day}"
        if ai_draft is not None:
            if self.models is None:
                raise TeleReadError(
                    "AI pre-grade without a model registry is an "
                    "unauditable opinion — refused")
            self.models.submit_draft(case_id, **ai_draft)
        self.cascade.setdefault(patient_id, {})["screened"] = day
        self.patient_cases.setdefault(patient_id, []).append(case_id)
        return self.reading.order(
            case_id, priority="routine",
            question="diabetic retinopathy screening grade",
            facility=facility, referrer=referrer, day=day,
            artifact="fundus-image", meta={"patient_id": patient_id,
                                           "eye": eye})

    def _case_of(self, patient_id):
        cases = self.patient_cases.get(patient_id)
        if not cases:
            raise TeleReadError("unknown screening patient")
        return cases[-1]

    def _finalize(self, patient_id, grade, day):
        out = {"final_grade": grade,
               "referred": grade in REFERRAL_GRADES}
        if grade in REFERRAL_GRADES:
            self.advance(patient_id, "referred", day=day)
        self.recall[patient_id] = recall_plan(grade, from_day=day)
        out["recall"] = dict(self.recall[patient_id])
        return out

    def record_grade(self, patient_id, *, grade, by, day):
        """One grade goes in; on the second, concordance runs.
        Agreement finalizes (referral + recall). Disagreement routes to
        arbitration — and a disputed CRITICAL grade is loud."""
        case_id = self._case_of(patient_id)
        self.grading.submit_grade(case_id, grade=grade, by=by, day=day)
        out = {"patient_id": patient_id, "case_id": case_id,
               "grade": grade, "by": by}
        c = self.grading.cases[case_id]
        if len(c["grades"]) < 2:
            out["status"] = "awaiting-second-grade"
            return out
        res = self.grading.resolve(case_id, day=day)
        out["resolution"] = res["status"]
        if res["status"] == "final-agreement":
            out.update(self._finalize(patient_id,
                                      res["final"]["grade"], day))
        else:
            claimed = {g["grade"] for g in res["grades"]}
            critical_claimed = claimed & set(CONFIG.critical_findings)
            out["disputed"] = sorted(claimed)
            if critical_claimed:
                out["critical_pending_arbitration"] = sorted(
                    critical_claimed)
        return out

    def arbitrate(self, patient_id, *, grade, by, day):
        case_id = self._case_of(patient_id)
        res = self.grading.arbitrate(case_id, grade=grade, by=by, day=day)
        out = {"patient_id": patient_id, "case_id": case_id,
               "arbiter": by}
        out.update(self._finalize(patient_id, res["final"]["grade"], day))
        return out

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

    def surveillance_due(self, as_of):
        """Recall worklist, overdue-first — the denominator is every
        patient ever graded, not every patient who came back."""
        due = [dict(plan, patient_id=pid)
               for pid, plan in self.recall.items()
               if plan["due_day"] <= as_of]
        due.sort(key=lambda p: p["due_day"])
        return due

    def cascade_report(self):
        out = {s: 0 for s in CASCADE}
        for c in self.cascade.values():
            for s in CASCADE:
                out[s] += int(s in c)
        out["note"] = ("each stage is a subset of the previous; "
                       "attrition between referred and attended is the "
                       "number that matters")
        return out
