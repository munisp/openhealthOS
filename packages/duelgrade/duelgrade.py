"""duelgrade.py — capture-time quality gates + two-grader arbitration.

The gap analysis put it plainly: a screening programme's credibility
hinges on its inputs and its concordance.

CaptureQA: an ungradable image is a FAILED SCREEN, not a negative.
Quality is assessed at capture against a versioned check list, every
check accounted for — 'did not look' is not a pass. A retake is a NEW
image; the QA ledger does not rewrite. Per-assessor ungradable rates
ship with denominators, because a camera programme that never looks at
its rejection rate is grading its inputs blind.

DuelGrader: one grade is an opinion; two make a concordance check.
Agreement is final. Disagreement routes to a senior arbiter who is NOT
a prior grader — a third pair of eyes, not a recount. An AI model may
hold the first read, but only through aigov governance: registered,
versioned, evidence-cited, research-only licences stay in shadow mode.

recall_plan: grade-driven surveillance intervals (ETDRS/DESP-shaped),
because a screen without a next appointment is a rumour.
"""


class DuelError(Exception):
    pass


# grade-driven recall, in 30-day months. Referral grades keep a
# post-treatment recall — 'referred' is not 'cured'.
SURVEILLANCE_MONTHS = {"no-dr": 12, "mild-npdr": 12, "moderate-npdr": 6,
                       "severe-npdr": 3, "pdr": 1, "maculopathy": 3}


def recall_plan(grade, *, from_day):
    """When is this patient due back? Fail-closed on unknown grades."""
    if grade not in SURVEILLANCE_MONTHS:
        raise DuelError(f"no surveillance interval for grade '{grade}'")
    months = SURVEILLANCE_MONTHS[grade]
    return {"grade": grade, "interval_months": months,
            "due_day": from_day + 30 * months,
            "note": "30-day months; ETDRS/DESP-shaped; referral grades "
                    "still get post-treatment recall"}


class CaptureQA:
    """Capture-time quality gate for one imaging domain."""

    def __init__(self, domain, checks):
        if not checks:
            raise DuelError("a QA gate without checks judges nothing")
        self.domain = domain
        self.checks = tuple(checks)
        self.records = {}   # image_ref -> assessment (append-only)

    def assess(self, image_ref, *, day, by, passed=(), failed=()):
        if not image_ref:
            raise DuelError("no image reference — nothing to assess")
        if not by:
            raise DuelError("unsigned QA is no QA — 'by' required")
        if image_ref in self.records:
            raise DuelError(
                f"{image_ref} already has a QA record — a retake is a "
                "new image, not a rewrite of the old one")
        unknown = (set(passed) | set(failed)) - set(self.checks)
        if unknown:
            raise DuelError(f"unknown checks: {sorted(unknown)}")
        overlap = set(passed) & set(failed)
        if overlap:
            raise DuelError(
                f"a check cannot both pass and fail: {sorted(overlap)}")
        unassessed = set(self.checks) - set(passed) - set(failed)
        if unassessed:
            raise DuelError(
                f"incomplete QA — unassessed: {sorted(unassessed)}; "
                "'did not look' is not a pass")
        rec = {"image_ref": image_ref, "domain": self.domain, "day": day,
               "by": by, "passed": list(passed), "failed": list(failed),
               "gradable": not failed}
        self.records[image_ref] = rec
        return dict(rec)

    def gradable(self, image_ref):
        """Absence is not a value: no record means NOT gradable."""
        rec = self.records.get(image_ref)
        return bool(rec and rec["gradable"])

    def report(self):
        """Ungradable rates with denominators, per failure mode and
        per assessor — the camera programme's own report card."""
        total = len(self.records)
        ungradable = [r for r in self.records.values() if not r["gradable"]]
        modes = {}
        for r in ungradable:
            for f in r["failed"]:
                modes[f] = modes.get(f, 0) + 1
        by_assessor = {}
        for r in self.records.values():
            b = by_assessor.setdefault(r["by"], {"n": 0, "ungradable": 0})
            b["n"] += 1
            b["ungradable"] += int(not r["gradable"])
        for b in by_assessor.values():
            b["ungradable_rate"] = round(b["ungradable"] / b["n"], 3)
        return {"domain": self.domain, "assessed": total,
                "ungradable": len(ungradable),
                "ungradable_rate": (round(len(ungradable) / total, 3)
                                    if total else None),
                "failure_modes": modes, "by_assessor": by_assessor,
                "note": "an ungradable image is a failed screen, not "
                        "a negative"}


class DuelGrader:
    """Two-grader concordance with senior arbitration (EyePACS-shaped)."""

    def __init__(self, readers, *, scope, grades, model_registry=None):
        if not grades:
            raise DuelError("a grader without a grade set invents labels")
        self.readers = readers
        self.scope = scope
        self.grades = tuple(grades)
        self.models = model_registry
        self.cases = {}     # case_id -> {grades, status, final}

    def _case(self, case_id):
        c = self.cases.get(case_id)
        if c is None:
            raise DuelError(f"unknown case: {case_id}")
        return c

    def _check_grade(self, grade):
        if grade not in self.grades:
            raise DuelError(f"unknown grade: {grade}")

    def _check_grader(self, by, day):
        if not by:
            raise DuelError("unsigned grades are rumours — 'by' required")
        if by.startswith("ai:"):
            return
        if not self.readers.credentialed(by, day):
            raise DuelError(
                f"grader '{by}' is not credentialed on day {day}")
        if not self.readers.has_scope(by, self.scope):
            raise DuelError(
                f"grader '{by}' lacks scope '{self.scope}' — a licence "
                "for one craft grades nothing in another")

    def submit_grade(self, case_id, *, grade, by, day):
        self._check_grade(grade)
        self._check_grader(by, day)
        c = self.cases.setdefault(
            case_id, {"grades": [], "status": "awaiting-grades",
                      "final": None})
        if c["status"].startswith("final"):
            raise DuelError("case closed — a final grade does not move; "
                            "a correction is a new case or an addendum")
        if c["status"] == "awaiting-arbitration":
            raise DuelError("grades are closed — arbitration is the only "
                            "door now")
        if any(g["by"] == by for g in c["grades"]):
            raise DuelError(
                "the second grader must be a second pair of eyes — "
                "self-agreement is not concordance")
        if len(c["grades"]) >= 2:
            raise DuelError("two grades recorded — resolve, don't pile on")
        c["grades"].append({"by": by, "grade": grade, "day": day})
        return {"case_id": case_id, "n_grades": len(c["grades"]),
                "status": c["status"]}

    def submit_ai_grade(self, case_id, *, grade, model_id, version, day):
        """AI holds the first read — but only through aigov governance:
        registered, versioned, evidence-cited; research-only licences
        never leave shadow mode."""
        if self.models is None:
            raise DuelError("an AI grade without a model registry is an "
                            "unauditable opinion — refused")
        self._check_grade(grade)
        self.models.submit_draft(case_id, model_id=model_id,
                                 version=version,
                                 output={"grade": grade, "role": "first-read"})
        return self.submit_grade(case_id, grade=grade,
                                 by=f"ai:{model_id}@{version}", day=day)

    def resolve(self, case_id, *, day):
        c = self._case(case_id)
        if c["status"].startswith("final"):
            raise DuelError("already final — concordance is not re-run "
                            "until it says what you want")
        if len(c["grades"]) < 2:
            raise DuelError("one grade is an opinion; two make a "
                            "concordance check")
        g1, g2 = c["grades"]
        if g1["grade"] == g2["grade"]:
            c["status"] = "final-agreement"
            c["final"] = {"grade": g1["grade"], "day": day,
                          "agreed_by": [g1["by"], g2["by"]]}
        else:
            c["status"] = "awaiting-arbitration"
        return dict(c, grades=list(c["grades"]))

    def arbitrate(self, case_id, *, grade, by, day):
        c = self._case(case_id)
        if c["status"] != "awaiting-arbitration":
            raise DuelError("nothing to arbitrate — arbitration follows "
                            "a disagreement, it does not pre-empt one")
        self._check_grade(grade)
        self._check_grader(by, day)
        priors = [g["by"] for g in c["grades"]]
        if by in priors:
            raise DuelError("the arbiter is a third pair of eyes, not a "
                            "recount by a grader who already decided")
        matched = next((g["by"] for g in c["grades"]
                        if g["grade"] == grade), None)
        c["status"] = "final-arbitrated"
        c["final"] = {"grade": grade, "arbiter": by, "day": day,
                      "matched": matched or "neither"}
        return dict(c, grades=list(c["grades"]))

    def final_grade(self, case_id):
        c = self._case(case_id)
        if not c["final"]:
            raise DuelError("no final grade — an unresolved grade is not "
                            "a result and decides nothing clinical")
        return c["final"]["grade"]

    def agreement_report(self):
        """Concordance with denominators. A rate without n is marketing."""
        decided = [c for c in self.cases.values()
                   if len(c["grades"]) >= 2]
        agreed = [c for c in decided if c["status"] == "final-agreement"]
        arbitrated = [c for c in decided
                      if c["status"] == "final-arbitrated"]
        per_grader = {}
        for c in decided:
            g1, g2 = c["grades"]
            same = g1["grade"] == g2["grade"]
            for g in (g1, g2):
                b = per_grader.setdefault(g["by"],
                                          {"paired": 0, "concordant": 0})
                b["paired"] += 1
                b["concordant"] += int(same)
        for b in per_grader.values():
            b["concordance"] = round(b["concordant"] / b["paired"], 3)
        n = len(decided)
        return {"cases_graded": n, "agreed": len(agreed),
                "arbitrated": len(arbitrated),
                "awaiting_arbitration": sum(
                    1 for c in decided
                    if c["status"] == "awaiting-arbitration"),
                "agreement_rate": (round(len(agreed) / n, 3)
                                   if n else None),
                "per_grader": per_grader,
                "note": "disagreement is data — it calibrates graders "
                        "and the AI alike"}
