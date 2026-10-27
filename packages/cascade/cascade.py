"""cascade.py — treatment cascade tracking for TB and HIV.

Cascade diseases kill through leakage: tested but never started,
started but never retained, retained but never suppressed. Every leak
is silent by default — the patient simply stops coming. This module
makes each step transition explicit and each missed appointment a
surfaced work item.

Cascades (publicly documented program stages):
  HIV: tested -> linked -> on-art -> retained-6m -> virally-suppressed
  TB:  screened -> diagnosed -> started -> completed -> cured

Doctrine:
- STRICT STAGE ORDER: like labtrack, a patient cannot jump stages —
  a "virally suppressed" with no "on-art" is a data error, refused.
- MISSED APPOINTMENT IS A WORK ITEM: expected return day recorded at
  each visit; overdue returns go on the tracer list, overdue-first.
- ICF (intensified case finding) HONESTY: an HIV test offer recorded
  without a result is unfinished, and unfinished offers are counted.
- RETENTION STATS CARRY DENOMINATORS: "90% retained" with n=10 is a
  different fact than n=10,000; both numbers always ship together.
"""

CASCADES = {
    "hiv": ["tested", "linked", "on-art", "retained-6m",
            "virally-suppressed"],
    "tb": ["screened", "diagnosed", "started", "completed", "cured"],
}
DEFAULT_RETURN_WINDOW_DAYS = 3  # grace past expected return


class CascadeError(Exception):
    pass


class CascadeRegister:
    def __init__(self, program):
        if program not in CASCADES:
            raise CascadeError(f"unknown program: {program}")
        self.program = program
        self.stages = CASCADES[program]
        self.patients = {}

    def enroll(self, patient_id, *, day, facility):
        if patient_id in self.patients:
            raise CascadeError(f"duplicate enrollment: {patient_id}")
        self.patients[patient_id] = {
            "facility": facility, "stage_idx": -1, "history": [],
            "expected_return": None, "active": True,
            "exit_reason": None,
        }
        return dict(self.patients[patient_id])

    def advance(self, patient_id, stage, *, day, result=None,
                expected_return_day=None):
        """Strictly sequential; result recorded at testing/diagnosis
        stages (positive/negative matter)."""
        p = self._pat(patient_id)
        if not p["active"]:
            raise CascadeError(f"{patient_id} exited: {p['exit_reason']}")
        if stage not in self.stages:
            raise CascadeError(f"unknown stage: {stage}")
        idx = self.stages.index(stage)
        if idx != p["stage_idx"] + 1:
            raise CascadeError(
                f"cannot jump from "
                f"{self.stages[p['stage_idx']] if p['stage_idx'] >= 0 else 'enrolled'} "
                f"to {stage} — sequential stages only")
        if stage in ("tested", "diagnosed") and result not in (
                "positive", "negative"):
            raise CascadeError(
                f"{stage} requires result positive|negative — "
                "an offer without a result is unfinished")
        p["stage_idx"] = idx
        p["history"].append((stage, day, result))
        p["expected_return"] = expected_return_day
        return dict(p)

    def exit(self, patient_id, *, day, reason):
        """died | transferred-out | lost-to-follow-up | stopped.
        LTFU is a program failure signal, recorded as such."""
        p = self._pat(patient_id)
        if reason not in ("died", "transferred-out",
                          "lost-to-follow-up", "stopped"):
            raise CascadeError(f"unknown exit reason: {reason}")
        p["active"] = False
        p["exit_reason"] = reason
        p["history"].append((f"exit:{reason}", day, None))
        return dict(p)

    def tracer_list(self, as_of, grace=DEFAULT_RETURN_WINDOW_DAYS):
        """Active patients past expected return + grace, overdue-first."""
        rows = []
        for pid, p in self.patients.items():
            if not p["active"] or p["expected_return"] is None:
                continue
            overdue = as_of - p["expected_return"] - grace
            if overdue > 0:
                rows.append({"patient_id": pid,
                             "stage": self.stages[p["stage_idx"]],
                             "days_overdue": overdue,
                             "facility": p["facility"]})
        rows.sort(key=lambda r: -r["days_overdue"])
        return rows

    def funnel(self, as_of):
        """Counts at each stage ever reached + exits, denominators visible."""
        counts = {s: 0 for s in self.stages}
        exits = {}
        for p in self.patients.values():
            reached = p["stage_idx"]
            for i in range(reached + 1):
                counts[self.stages[i]] += 1
            if not p["active"]:
                exits[p["exit_reason"]] = exits.get(p["exit_reason"], 0) + 1
        return {"program": self.program, "as_of": as_of,
                "enrolled": len(self.patients), "stage_counts": counts,
                "exits": exits,
                "note": "stage_counts are cumulative-ever-reached; "
                        "compare adjacent stages to find the leak"}

    def _pat(self, pid):
        if pid not in self.patients:
            raise CascadeError(f"unknown patient: {pid}")
        return self.patients[pid]
