"""surgicalsafety.py — surgical safety checklist as code + EmONC audit.

The WHO Surgical Safety Checklist cuts surgical mortality by a third
where it's actually used — and is a laminated poster nobody reads in
most theatres. As code, a skipped phase is a hard stop, not a poster.

Also here: EmONC signal-function audit — whether a facility can
actually perform the emergency obstetric functions it claims
(publicly documented UN signal functions). A facility that calls
itself comprehensive but hasn't done a transfusion in 3 months is
mislabelled; the audit says so.

Doctrine:
- CHECKLIST PHASES ARE SEQUENTIAL AND COMPLETE: sign-in -> time-out ->
  sign-out. Every item in a phase must be affirmed; a phase with an
  unanswered item cannot complete; time-out before sign-in is refused.
- ITEMS ANSWERED 'NO' ARE DOCUMENTED EXCEPTIONS: proceeding with a
  'no' requires a reason (e.g. antibiotic allergy -> alternative
  given) — a blank override is refused.
- EMONC LABELS ARE EARNED: the audit computes the label from recorded
  signal functions in the trailing period; a facility may not
  self-declare what its records contradict.
"""

PHASES = ("sign-in", "time-out", "sign-out")
CHECKLIST = {
    "sign-in": ["identity-confirmed", "site-marked", "anaesthesia-check",
                "airway-risk-assessed", "allergy-known"],
    "time-out": ["team-introductions", "procedure-confirmed",
                 "antibiotic-prophylaxis-given",
                 "blood-loss-plan", "equipment-sterility-confirmed"],
    "sign-out": ["procedure-recorded", "counts-complete",
                 "specimens-labelled", "recovery-plan-stated"],
}

EMONC_BASIC = ("oxytocin", "anticonvulsants", "antibiotics",
               "manual-removal-placenta", "removal-retained-products",
               "assisted-vaginal-delivery", "neonatal-resuscitation")
EMONC_COMPREHENSIVE = EMONC_BASIC + ("caesarean", "blood-transfusion")
EMONC_WINDOW_DAYS = 90


class SurgicalError(Exception):
    pass


class ChecklistSession:
    def __init__(self, case_id, *, team_lead, day):
        self.case_id = case_id
        self.team_lead = team_lead
        self.day = day
        self.phase_idx = -1
        self.answers = {}          # phase -> {item: (bool, reason|None)}
        self.completed = []

    def start_phase(self, phase):
        if phase not in PHASES:
            raise SurgicalError(f"unknown phase: {phase}")
        idx = PHASES.index(phase)
        if idx != self.phase_idx + 1:
            raise SurgicalError(
                f"phases are sequential: next is "
                f"{PHASES[self.phase_idx + 1] if self.phase_idx + 1 < 3 else 'done'}")
        self.phase_idx = idx
        self.answers[phase] = {}
        return phase

    def answer(self, item, affirmed, *, reason=None):
        if self.phase_idx < 0:
            raise SurgicalError("no phase started")
        phase = PHASES[self.phase_idx]
        if item not in CHECKLIST[phase]:
            raise SurgicalError(f"unknown item for {phase}: {item}")
        if not affirmed and not reason:
            raise SurgicalError(
                f"'{item}' answered no — a documented reason is "
                "required to proceed (blank override refused)")
        self.answers[phase][item] = (bool(affirmed), reason)

    def complete_phase(self):
        if self.phase_idx < 0:
            raise SurgicalError("no phase started")
        phase = PHASES[self.phase_idx]
        missing = [i for i in CHECKLIST[phase] if i not in self.answers[phase]]
        if missing:
            raise SurgicalError(
                f"{phase} incomplete — unanswered: {', '.join(missing)}")
        self.completed.append(phase)
        exceptions = {i: r for i, (ok, r) in self.answers[phase].items()
                      if not ok}
        return {"phase": phase, "complete": True, "exceptions": exceptions}

    def report(self):
        return {"case_id": self.case_id, "team_lead": self.team_lead,
                "phases_completed": list(self.completed),
                "fully_complete": len(self.completed) == 3,
                "exceptions": {
                    p: {i: r for i, (ok, r) in self.answers[p].items()
                        if not ok}
                    for p in self.completed}}


class EmONCAudit:
    """Signal functions actually performed in the trailing window."""

    def __init__(self, facility):
        self.facility = facility
        self.events = []  # (signal_function, day)

    def record(self, signal_function, *, day):
        if signal_function not in EMONC_COMPREHENSIVE:
            raise EmONCError(f"unknown signal function: {signal_function}")
        self.events.append((signal_function, day))

    def audit(self, as_of, window=EMONC_WINDOW_DAYS):
        done = {sf for sf, d in self.events if as_of - window <= d <= as_of}
        missing_basic = [sf for sf in EMONC_BASIC if sf not in done]
        missing_comp = [sf for sf in EMONC_COMPREHENSIVE if sf not in done]
        if not missing_comp:
            label = "comprehensive-emonc"
        elif not missing_basic:
            label = "basic-emonc"
        else:
            label = "not-emonc"
        return {"facility": self.facility, "as_of": as_of,
                "window_days": window, "label": label,
                "missing_basic": missing_basic,
                "missing_comprehensive": missing_comp,
                "note": "label computed from recorded functions — "
                        "a facility earns EmONC, it doesn't declare it"}


class EmONCError(Exception):
    pass
