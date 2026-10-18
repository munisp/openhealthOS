"""Operating-theatre lists and the WHO surgical safety checklist.

A Theatre runs one list per day. Cases are booked onto a list with a
priority (elective / urgent / emergency) — an emergency case inserts ahead
of electives rather than failing, because that is what real theatres do.
No case starts until its WHO checklist sign-in is complete; no case closes
until sign-out is complete. The checklist phases are the WHO's own:
sign-in (before induction), time-out (before incision), sign-out (before
the patient leaves the room).
"""

PRIORITIES = {"elective": 2, "urgent": 1, "emergency": 0}
PHASES = ("sign_in", "time_out", "sign_out")

SIGN_IN_ITEMS = (
    "identity_site_procedure_confirmed", "site_marked", "anaesthesia_check",
    "airway_risk_assessed", "blood_loss_plan",
)
TIME_OUT_ITEMS = (
    "team_introductions", "patient_identity_reconfirmed", "antibiotic_given",
    "anticipated_critical_events",
)
SIGN_OUT_ITEMS = (
    "procedure_recorded", "counts_complete", "specimens_labelled",
    "equipment_issues_addressed", "recovery_plan",
)

CHECKLIST = {"sign_in": SIGN_IN_ITEMS, "time_out": TIME_OUT_ITEMS,
             "sign_out": SIGN_OUT_ITEMS}


class Error(Exception):
    pass


class Case:
    def __init__(self, case_id, patient_id, procedure, priority, surgeon,
                 anaesthetist, episode_id=None):
        if priority not in PRIORITIES:
            raise Error(f"unknown priority: {priority}")
        if not surgeon or not anaesthetist:
            raise Error("every case names a surgeon and an anaesthetist")
        self.id = case_id
        self.patient_id = patient_id
        self.episode_id = episode_id  # link to packages/inpatient when inpatient
        self.procedure = procedure
        self.priority = priority
        self.surgeon = surgeon
        self.anaesthetist = anaesthetist
        self.state = "booked"  # booked -> in_progress -> completed | cancelled
        self.checklist = {ph: set() for ph in PHASES}
        self.outcome = ""

    def checklist_complete(self, phase):
        return self.checklist[phase] == set(CHECKLIST[phase])


class Theatre:
    def __init__(self, theatre_id):
        self.id = theatre_id
        self.lists = {}   # date -> list of case ids in running order
        self.cases = {}
        self._seq = 0

    def book(self, date, patient_id, procedure, priority, surgeon,
             anaesthetist, episode_id=None):
        self._seq += 1
        c = Case(f"SURG-{self._seq:05d}", patient_id, procedure, priority,
                 surgeon, anaesthetist, episode_id)
        self.cases[c.id] = c
        run = self.lists.setdefault(date, [])
        run.append(c.id)
        # emergency bump: sort by priority, stable within a priority
        run.sort(key=lambda cid: PRIORITIES[self.cases[cid].priority])
        return c

    def running_order(self, date):
        return list(self.lists.get(date, []))

    def check(self, case_id, phase, item, actor):
        c = self._case(case_id)
        if phase not in PHASES:
            raise Error(f"unknown phase: {phase}")
        if item not in CHECKLIST[phase]:
            raise Error(f"not a WHO {phase} item: {item}")
        if not actor:
            raise Error("checklist items name who confirmed them")
        if phase == "time_out" and not c.checklist_complete("sign_in"):
            raise Error("sign-in must be complete before time-out")
        if phase == "sign_out" and not c.checklist_complete("time_out"):
            raise Error("time-out must be complete before sign-out")
        c.checklist[phase].add(item)
        return c

    def start(self, case_id, actor):
        c = self._case(case_id)
        if c.state != "booked":
            raise Error(f"case {case_id} is {c.state}")
        if not c.checklist_complete("sign_in"):
            missing = sorted(set(SIGN_IN_ITEMS) - c.checklist["sign_in"])
            raise Error(f"cannot start — sign-in incomplete: {missing}")
        for other in self.cases.values():
            if other.state == "in_progress":
                raise Error(f"theatre occupied by {other.id}")
        c.state = "in_progress"
        return c

    def complete(self, case_id, actor, outcome=""):
        c = self._case(case_id)
        if c.state != "in_progress":
            raise Error(f"case {case_id} is {c.state}")
        if not c.checklist_complete("sign_out"):
            missing = sorted(set(SIGN_OUT_ITEMS) - c.checklist["sign_out"])
            raise Error(f"cannot close — sign-out incomplete: {missing}")
        c.state = "completed"
        c.outcome = outcome
        return c

    def cancel(self, case_id, actor, reason):
        c = self._case(case_id)
        if c.state == "completed":
            raise Error("cannot cancel a completed case")
        if not reason:
            raise Error("cancellations need a reason (theatre time is scarce)")
        c.state = "cancelled"
        c.outcome = reason
        return c

    def _case(self, case_id):
        if case_id not in self.cases:
            raise Error(f"unknown case: {case_id}")
        return self.cases[case_id]

    def utilisation(self, date):
        """Share of the day's booked cases that ran or were cancelled."""
        run = self.lists.get(date, [])
        done = sum(1 for cid in run if self.cases[cid].state in ("completed", "cancelled"))
        return {"list_size": len(run), "resolved": done,
                "utilisation_pct": round(100.0 * done / len(run), 1) if run else 0.0}
