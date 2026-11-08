"""rehab.py — physiotherapy and rehabilitation: goals, sessions, outcomes.

Rehabilitation is measured in function regained, not sessions billed.
A course of physiotherapy that cannot show its baseline and its outcome
is billing, not therapy.

Doctrine:
- GOALS ARE SMART-ENOUGH: a goal names the measure, the baseline, the
  target, and the review day. "Improve mobility" is a wish; "knee
  flexion 40° → 90° by day 28" is a goal.
- SESSIONS RECORD DOSAGE: modality, duration, and the patient's
  response. Sessions without the patient present (DNA) are recorded as
  DNA, not as sessions.
- OUTCOME MEASURES ARE VERSIONED: each measure names its instrument
  (ROM degrees, MRC grade, Barthel, TUG seconds) so a number means the
  same thing next year.
- A COURSE CLOSES WITH A DISCHARGE SUMMARY: goals met / partially met /
  not met, per goal, never silently archived.
"""

import hashlib
import json

VERSION = "rehab@1.0.0"

MEASURES = ("rom-degrees", "mrc-grade", "barthel", "tug-seconds",
            "pain-vas", "distance-metres")
GOAL_STATES = ("active", "met", "partially-met", "not-met", "abandoned")
SESSION_STATES = ("completed", "dna", "cancelled")

# plausible bounds per measure
_BOUNDS = {
    "rom-degrees": (0, 360),
    "mrc-grade": (0, 5),
    "barthel": (0, 100),
    "tug-seconds": (0, 600),
    "pain-vas": (0, 10),
    "distance-metres": (0, 10000),
}


class Error(Exception):
    pass


class RehabGoal:
    def __init__(self, goal_id, measure, baseline, target, review_day):
        if measure not in MEASURES:
            raise Error(f"unknown measure: {measure}")
        lo, hi = _BOUNDS[measure]
        if not (lo <= baseline <= hi):
            raise Error(f"baseline {baseline} outside {lo}–{hi}")
        if not (lo <= target <= hi):
            raise Error(f"target {target} outside {lo}–{hi}")
        self.goal_id = goal_id
        self.measure = measure
        self.baseline = baseline
        self.target = target
        self.review_day = review_day
        self.state = "active"
        self.outcome_value = None

    def close(self, state, *, outcome_value, by, day):
        if self.state != "active":
            raise Error("goal already closed")
        if state not in ("met", "partially-met", "not-met", "abandoned"):
            raise Error(f"unknown goal state: {state}")
        lo, hi = _BOUNDS[self.measure]
        if not (lo <= outcome_value <= hi):
            raise Error(f"outcome {outcome_value} outside {lo}–{hi}")
        if not by:
            raise Error("a goal outcome names the assessor")
        self.state = state
        self.outcome_value = outcome_value
        self.closed_by = by
        self.closed_day = day


class RehabCourse:
    def __init__(self, course_id, patient_ref, condition, *, therapist,
                 start_day):
        for name, val in (("course", course_id), ("patient", patient_ref),
                          ("condition", condition),
                          ("therapist", therapist)):
            if not val:
                raise Error(f"a rehab course names its {name}")
        self.course_id = course_id
        self.patient_ref = patient_ref
        self.condition = condition
        self.therapist = therapist
        self.start_day = start_day
        self.goals = {}
        self.sessions = []
        self.discharge = None

    def add_goal(self, goal_id, measure, baseline, target, review_day):
        if self.discharge:
            raise Error("course discharged — no new goals")
        if goal_id in self.goals:
            raise Error(f"goal {goal_id} already exists")
        g = RehabGoal(goal_id, measure, baseline, target, review_day)
        self.goals[goal_id] = g
        return g

    def record_session(self, *, day, modality, duration_minutes,
                       patient_response, state="completed"):
        if self.discharge:
            raise Error("course discharged — no further sessions")
        if state not in SESSION_STATES:
            raise Error(f"unknown session state: {state}")
        if state == "completed":
            if not (modality and modality.strip()):
                raise Error("a session names its modality")
            if not (0 < duration_minutes <= 480):
                raise Error("implausible session duration")
        ev = {"day": len(self.sessions) + 1, "modality": modality,
              "duration_minutes": duration_minutes if state == "completed"
              else 0,
              "patient_response": patient_response, "state": state}
        self.sessions.append(ev)
        return ev

    def discharge_summary(self, *, by, day):
        """Every active goal must be closed first — no silent archives."""
        open_goals = [g.goal_id for g in self.goals.values()
                      if g.state == "active"]
        if open_goals:
            raise Error(
                f"goals still open: {open_goals} — close each with its "
                "outcome before discharge")
        completed = [s for s in self.sessions if s["state"] == "completed"]
        dna = [s for s in self.sessions if s["state"] == "dna"]
        self.discharge = {
            "by": by, "day": day,
            "goals": {gid: {"state": g.state,
                            "baseline": g.baseline,
                            "target": g.target,
                            "outcome": g.outcome_value}
                      for gid, g in self.goals.items()},
            "sessions_completed": len(completed),
            "sessions_dna": len(dna),
        }
        return self.discharge

    def as_dict(self):
        body = {"course_id": self.course_id,
                "patient_ref": self.patient_ref,
                "condition": self.condition,
                "therapist": self.therapist,
                "goals": {gid: {"measure": g.measure,
                                "baseline": g.baseline,
                                "target": g.target,
                                "state": g.state,
                                "outcome": g.outcome_value}
                          for gid, g in self.goals.items()},
                "sessions": self.sessions,
                "discharge": self.discharge, "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
