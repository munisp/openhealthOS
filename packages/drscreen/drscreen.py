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

# ---- pregnancy pathway + treatment tracking (gap: P1) ----
# Pregnant diabetics have their own clock: pregnancy accelerates
# retinopathy, so the grade-driven interval is overridden by a
# trimester rhythm (NICE-shaped: screen each trimester), and the
# override is RECORDED — the original grade plan is preserved.
PREGNANCY_RECALL_MONTHS = 3
PREGNANCY_MAX_DAYS = 294        # lmp-to-today sanity: 42 weeks
# anti-VEGF is a SERIES, not an event: a broken series is a relapse
# risk. Loading-phase interval is ~monthly; longer gaps are flagged.
DR_TREATMENTS = ("anti-vegf-injection", "laser-prp", "laser-focal",
                 "vitrectomy")
INJECTION_INTERVAL_DAYS = 28
SERIES_GAP_FLAG_DAYS = 84       # > ~12 weeks between injections

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
        self.pregnancy = {}      # patient_id -> {lmp_day, ...}
        self.treatments = {}     # (patient_id, eye) -> [episodes]

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
        plan = recall_plan(grade, from_day=day)
        # pregnancy accelerates SURVEILLANCE; a referral grade keeps
        # its urgent rhythm — pregnancy is no reason to wait on PDR
        if grade not in REFERRAL_GRADES and \
                patient_id in self.pregnancy and \
                not self.pregnancy[patient_id].get("ended"):
            # pregnancy overrides the grade interval — recorded, not
            # rewritten: the grade plan is preserved alongside
            plan = {"grade": plan["grade"],
                    "interval_months": PREGNANCY_RECALL_MONTHS,
                    "due_day": day + 30 * PREGNANCY_RECALL_MONTHS,
                    "pregnancy_override": True,
                    "grade_plan_months": plan["interval_months"]}
            out["pregnancy_override"] = True
        self.recall[patient_id] = plan
        out["recall"] = dict(self.recall[patient_id])
        return out

    # ---- pregnancy pathway ----
    def mark_pregnant(self, patient_id, *, day, lmp_day):
        """Pregnancy replaces the surveillance clock. The LMP is
        sanity-checked; the override is appended, never rewritten."""
        if patient_id in self.pregnancy and \
                not self.pregnancy[patient_id].get("ended"):
            raise TeleReadError("already marked pregnant — a second "
                                "mark is a new pregnancy, not an edit")
        if lmp_day is None or not (0 <= day - lmp_day
                                   <= PREGNANCY_MAX_DAYS):
            raise TeleReadError(
                f"lmp implies {None if lmp_day is None else day - lmp_day} "
                "days gestation — outside 0..294 the date is wrong, "
                "not the patient")
        self.pregnancy[patient_id] = {"lmp_day": lmp_day,
                                      "marked_day": day, "ended": None}
        # if a recall plan already exists, override it now — but only
        # surveillance grades; referral keeps its urgent rhythm
        if patient_id in self.recall:
            plan = self.recall[patient_id]
            if plan["grade"] not in REFERRAL_GRADES and \
                    not plan.get("pregnancy_override"):
                self.recall[patient_id] = {
                    "grade": plan["grade"],
                    "interval_months": PREGNANCY_RECALL_MONTHS,
                    "due_day": day + 30 * PREGNANCY_RECALL_MONTHS,
                    "pregnancy_override": True,
                    "grade_plan_months": plan["interval_months"]}
        return dict(self.pregnancy[patient_id])

    def end_pregnancy(self, patient_id, *, day, outcome):
        """Delivery (or loss) restores the grade-driven clock from the
        last grade. The pregnancy record stays — the ledger does not
        erase."""
        preg = self.pregnancy.get(patient_id)
        if not preg or preg.get("ended"):
            raise TeleReadError("no open pregnancy for this patient")
        if outcome not in ("delivered", "pregnancy-loss"):
            raise TeleReadError(f"unknown pregnancy outcome: {outcome}")
        preg["ended"] = {"day": day, "outcome": outcome}
        if patient_id in self.recall:
            plan = self.recall[patient_id]
            if plan.get("pregnancy_override"):
                self.recall[patient_id] = recall_plan(
                    plan["grade"], from_day=day)
        return dict(preg)

    # ---- treatment tracking ----
    def record_treatment(self, patient_id, *, kind, eye, day, by):
        """Treatment episodes close the cascade loop. Gates: only a
        referral-grade patient is treated (treating mild disease is a
        crossed wire), and every episode is signed and sited."""
        if kind not in DR_TREATMENTS:
            raise TeleReadError(f"unknown treatment: {kind}")
        if eye not in ("left", "right"):
            raise TeleReadError("treatment is sited: left or right — "
                                "'the eye' is not an answer")
        if not by:
            raise TeleReadError("unsigned treatment is no treatment")
        plan = self.recall.get(patient_id)
        if not plan or plan["grade"] not in REFERRAL_GRADES:
            raise TeleReadError(
                "treatment requires a referral-grade final grade — "
                "treating non-referable disease is harm with a "
                "procedure code")
        ep = {"kind": kind, "eye": eye, "day": day, "by": by}
        key = (patient_id, eye)
        series = self.treatments.setdefault(key, [])
        if series and day < series[-1]["day"]:
            raise TeleReadError("episode predates the previous one — "
                                "the series is append-only")
        series.append(ep)
        # the cascade learns the loop closed — but the cascade
        # is sequential: treatment without attendance is unverifiable
        c = self.cascade.get(patient_id, {})
        if "attended" in c and "treated" not in c:
            c["treated"] = day
        return dict(ep)

    def injection_series(self, patient_id, eye):
        """The anti-VEGF series for one eye: count, rhythm, and the
        gaps that predict relapse."""
        series = [e for e in self.treatments.get((patient_id, eye), [])
                  if e["kind"] == "anti-vegf-injection"]
        gaps = [series[i + 1]["day"] - series[i]["day"]
                for i in range(len(series) - 1)]
        return {"patient_id": patient_id, "eye": eye,
                "injections": len(series),
                "first_day": series[0]["day"] if series else None,
                "last_day": series[-1]["day"] if series else None,
                "gaps_days": gaps,
                "broken_gaps": [g for g in gaps
                                if g > SERIES_GAP_FLAG_DAYS],
                "note": "a gap beyond 84 days is a broken series — a "
                        "relapse risk, not a scheduling detail"}

    def treatment_due(self, as_of):
        """Anti-VEGF patients past their next-injection date, plus
        referral-grade patients never treated at all — overdue-first."""
        out = []
        for (pid, eye), series in self.treatments.items():
            inj = [e for e in series
                   if e["kind"] == "anti-vegf-injection"]
            if not inj:
                continue
            next_due = inj[-1]["day"] + INJECTION_INTERVAL_DAYS
            if next_due <= as_of:
                out.append({"patient_id": pid, "eye": eye,
                            "reason": "injection-due",
                            "next_due_day": next_due,
                            "overdue_days": as_of - next_due})
        for pid, plan in self.recall.items():
            if plan["grade"] in REFERRAL_GRADES \
                    and not any(p == pid for p, _ in self.treatments):
                c = self.cascade.get(pid, {})
                ref_day = c.get("referred", 0)
                out.append({"patient_id": pid, "eye": None,
                            "reason": "referred-never-treated",
                            "next_due_day": ref_day,
                            "overdue_days": as_of - ref_day})
        out.sort(key=lambda x: -x["overdue_days"])
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
