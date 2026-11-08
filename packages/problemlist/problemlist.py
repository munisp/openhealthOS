"""problemlist.py — the longitudinal problem list.

The problem list is the clinical spine of the record: what is wrong with
this patient, since when, and is it active. Every other module reads it;
nothing else owns it. A diagnosis in a note that never reaches the
problem list is a fact the system cannot act on.

Doctrine:
- PROBLEMS ARE CODED: each entry carries an ICD-10 code (or an explicit
  'uncoded' flag — absence is stated, not blank).
- ACTIVE / RESOLVED / RULED-OUT: a problem never disappears; it changes
  state with a stated reason and a named clinician.
- CHRONICITY IS COMPUTED: a problem active beyond 90 days is chronic —
  the system notices, not the doctor's memory.
- THE LIST IS CHECKSUMMED: the patient's problems at a point in time
  carry a sha256 — the list that cannot be quietly edited.
"""

import hashlib
import json

VERSION = "problemlist@1.0.0"

STATES = ("active", "resolved", "ruled-out", "recurrence")
CHRONIC_DAYS = 90

# minimal ICD-10 validation: letter + 2 digits + optional . + 1-2 digits
import re
_ICD10 = re.compile(r"^[A-Z]\d{2}(\.\d{1,2})?$")


class Error(Exception):
    pass


class Problem:
    def __init__(self, problem_id, patient_ref, *, title, icd10=None,
                 onset_day, recorded_by, day):
        if not (title and title.strip()):
            raise Error("a problem names itself")
        if icd10 is not None and not _ICD10.match(icd10):
            raise Error(f"malformed ICD-10: {icd10}")
        if not recorded_by:
            raise Error("a problem names its recorder")
        self.problem_id = problem_id
        self.patient_ref = patient_ref
        self.title = title
        self.icd10 = icd10
        self.uncoded = icd10 is None
        self.onset_day = onset_day
        self.recorded_by = recorded_by
        self.recorded_day = day
        self.state = "active"
        self.history = [{"state": "active", "day": day,
                         "by": recorded_by, "reason": "initial entry"}]

    def _change(self, state, *, by, day, reason):
        if state not in STATES:
            raise Error(f"unknown state: {state}")
        if not by:
            raise Error("a state change names the clinician")
        if not (reason and reason.strip()):
            raise Error("a state change states its reason")
        self.state = state
        self.history.append({"state": state, "day": day, "by": by,
                             "reason": reason})

    def resolve(self, *, by, day, reason):
        self._change("resolved", by=by, day=day, reason=reason)

    def rule_out(self, *, by, day, reason):
        self._change("ruled-out", by=by, day=day, reason=reason)

    def recur(self, *, by, day, reason):
        if self.state not in ("resolved", "ruled-out"):
            raise Error("only a resolved or ruled-out problem recurs")
        self._change("recurrence", by=by, day=day, reason=reason)

    def is_chronic(self, day):
        active_days = day - self.onset_day
        return self.state in ("active", "recurrence") \
            and active_days >= CHRONIC_DAYS


class ProblemList:
    def __init__(self, patient_ref):
        if not patient_ref:
            raise Error("a problem list belongs to a patient")
        self.patient_ref = patient_ref
        self.problems = {}
        self._n = 0

    def add(self, *, title, icd10=None, onset_day, recorded_by, day):
        self._n += 1
        pid = f"{self.patient_ref}-P{self._n}"
        p = Problem(pid, self.patient_ref, title=title, icd10=icd10,
                    onset_day=onset_day, recorded_by=recorded_by, day=day)
        self.problems[pid] = p
        return p

    def active(self, day):
        return [p for p in self.problems.values()
                if p.state in ("active", "recurrence")]

    def chronic(self, day):
        return [p for p in self.active(day) if p.is_chronic(day)]

    def snapshot(self, day):
        body = {"patient_ref": self.patient_ref, "day": day,
                "problems": [
                    {"problem_id": p.problem_id, "title": p.title,
                     "icd10": p.icd10, "uncoded": p.uncoded,
                     "state": p.state, "onset_day": p.onset_day,
                     "chronic": p.is_chronic(day)}
                    for p in self.problems.values()],
                "version": VERSION}
        body["counts"] = {
            "total": len(self.problems),
            "active": len([p for p in self.problems.values()
                           if p.state == "active"]),
            "chronic": len(self.chronic(day)),
            "uncoded": len([p for p in self.problems.values()
                            if p.uncoded]),
        }
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
