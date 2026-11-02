"""callrecall.py — the population register that drives a screening
programme.

A screening programme is not the test; it is the REGISTER: who is
eligible, who has been invited, who did not come, who is due again.
Without the call/recall ladder, screening is a one-off event for
whoever happened to walk in — and the women most at risk are the
ones who never walk in.

Doctrine:
  - eligibility is a GATE, not a suggestion: outside the age band,
    enrolment refuses (an ineligible invitation is wasted resource
    and false reassurance)
  - the invitation ladder is sequential: invite → reminder-1 →
    reminder-2, each with a channel recorded — an invite without a
    channel never happened
  - unanswered after the ladder → lapsed, loudly: a name on a
    community follow-up list, not a silent statistic
  - exits are explicit and append-only: the register does not erase;
    a woman with no cervix must not be invited forever
  - coverage is a rate WITH its denominator — a rate without n is
    marketing
"""

CALL_RECALL_VERSION = "call-recall@1.0.0"

# WHO-shaped eligibility: general-population women 30-49 every 60
# months; women living with HIV 25-49 every 36 months.
PROGRAMS = {
    "cervix": {
        "age_band": (30, 49),
        "age_band_hiv": (25, 49),
        "interval_months": 60,
        "interval_months_hiv": 36,
    },
}

LADDER = (("invite", 0), ("reminder-1", 14), ("reminder-2", 42))
LAPSED_AFTER_DAYS = 70        # past this unanswered → CHW follow-up
CHANNELS = ("sms", "phone", "letter", "chw-visit", "in-person")
EXIT_REASONS = ("aged-out", "deceased", "moved-away", "opted-out",
                "no-cervix")
DAYS_PER_YEAR = 365


class CallRecallError(Exception):
    pass


class CallRecallRegister:
    def __init__(self, program="cervix"):
        if program not in PROGRAMS:
            raise CallRecallError(f"unknown screening programme: {program}")
        self.program = program
        self.spec = PROGRAMS[program]
        self.enrolled = {}   # patient_id -> record

    # ---- enrolment ----
    def _age(self, birth_day, day):
        return (day - birth_day) // DAYS_PER_YEAR

    def _band(self, hiv_positive):
        return (self.spec["age_band_hiv"] if hiv_positive
                else self.spec["age_band"])

    def enroll(self, patient_id, *, birth_day, day, hiv_positive=False):
        if patient_id in self.enrolled:
            raise CallRecallError(f"already enrolled: {patient_id}")
        lo, hi = self._band(hiv_positive)
        age = self._age(birth_day, day)
        if not (lo <= age <= hi):
            raise CallRecallError(
                f"age {age} outside the {self.program} eligibility band "
                f"{lo}-{hi}{' (HIV band)' if hiv_positive else ''} — "
                "screening the ineligible is resource spent on the "
                "wrong woman and reassurance she cannot use")
        self.enrolled[patient_id] = {
            "patient_id": patient_id, "birth_day": birth_day,
            "hiv_positive": bool(hiv_positive),
            "enrolled_day": day,
            "last_screened_day": None,     # never screened → due NOW
            "round_start": day,            # the current invitation round
            "steps": [],                   # append-only ladder records
            "exits": [],                   # append-only; register keeps her
            "active": True,
            "register_version": CALL_RECALL_VERSION}
        return dict(self.enrolled[patient_id])

    def update_hiv_status(self, patient_id, *, hiv_positive, day):
        """HIV status is a registry FACT that changes the interval and
        the band — record the change, don't rewrite history."""
        r = self._r(patient_id)
        if r["hiv_positive"] == bool(hiv_positive):
            raise CallRecallError("status unchanged — nothing to record")
        r["hiv_positive"] = bool(hiv_positive)
        r.setdefault("hiv_changes", []).append(
            {"hiv_positive": bool(hiv_positive), "day": day})
        return dict(r)

    def exit(self, patient_id, *, day, reason):
        """Exits are explicit and append-only. 'no-cervix' matters: a
        programme without exit rules invites the wrong women forever."""
        if reason not in EXIT_REASONS:
            raise CallRecallError(f"unknown exit reason: {reason}")
        r = self._r(patient_id)
        if not r["active"]:
            raise CallRecallError("already exited — the register does "
                                  "not erase, and it does not double-exit")
        r["exits"].append({"reason": reason, "day": day})
        r["active"] = False
        return dict(r)

    # ---- due dates ----
    def interval_days(self, patient_id):
        r = self._r(patient_id)
        months = (self.spec["interval_months_hiv"] if r["hiv_positive"]
                  else self.spec["interval_months"])
        return 30 * months

    def due_day(self, patient_id):
        r = self._r(patient_id)
        if r["last_screened_day"] is None:
            return r["round_start"]       # eligible and never screened
        return r["last_screened_day"] + self.interval_days(patient_id)

    # ---- the invitation ladder ----
    def record_step(self, patient_id, *, step, day, channel):
        r = self._r(patient_id)
        if not r["active"]:
            raise CallRecallError("exited from the register — inviting "
                                  "her anyway is a letter to nowhere")
        if step not in dict(LADDER):
            raise CallRecallError(f"unknown ladder step: {step}")
        if channel not in CHANNELS:
            raise CallRecallError(
                f"unknown channel: {channel} — an invite without a "
                "channel never happened")
        done = [s["step"] for s in r["steps"]]
        expected = [s for s, _ in LADDER][len(done)] \
            if len(done) < len(LADDER) else None
        if step != expected:
            raise CallRecallError(
                f"the ladder is sequential: next step is {expected!r}, "
                f"not {step!r} — nagging out of order is how registers "
                "lose the plot")
        r["steps"].append({"step": step, "day": day, "channel": channel})
        return dict(r)

    def _round_closed(self, r):
        """A round closes when she screens; steps then belong to the
        NEXT round."""
        return (r["last_screened_day"] is not None
                and r["last_screened_day"] >= r["round_start"])

    def steps_due(self, as_of):
        """The ladder worklist: for every active woman past her due
        date, the next unrecorded step — overdue-first."""
        out = []
        for pid, r in self.enrolled.items():
            if not r["active"] or self.due_day(pid) > as_of:
                continue
            if self._round_closed(r):
                continue
            done = len(r["steps"])
            if done >= len(LADDER):
                continue              # ladder exhausted → lapsed list
            step, offset = LADDER[done]
            step_due = r["round_start"] + offset
            if step_due <= as_of:
                out.append({"patient_id": pid, "step": step,
                            "due_day": step_due,
                            "overdue_days": as_of - step_due})
        out.sort(key=lambda x: (-x["overdue_days"], x["patient_id"]))
        return out

    def lapsed(self, as_of):
        """Ladder exhausted and still unscreened — community follow-up
        list, oldest first. These are the women the programme exists
        for and is failing."""
        out = []
        for pid, r in self.enrolled.items():
            if not r["active"] or self._round_closed(r):
                continue
            if len(r["steps"]) >= len(LADDER) and \
                    r["round_start"] + LAPSED_AFTER_DAYS <= as_of:
                out.append({"patient_id": pid,
                            "days_since_due": as_of - r["round_start"],
                            "steps_sent": len(r["steps"])})
        out.sort(key=lambda x: -x["days_since_due"])
        return out

    # ---- screening closes the loop ----
    def mark_screened(self, patient_id, *, day):
        r = self._r(patient_id)
        if not r["active"]:
            raise CallRecallError("exited from the register — a screen "
                                  "cannot re-open it")
        r["last_screened_day"] = day
        r["round_start"] = day + self.interval_days(patient_id)
        r["steps"] = []                # next round starts a fresh ladder
        return dict(r)

    # ---- coverage, with denominators ----
    def coverage(self, as_of):
        active = [r for r in self.enrolled.values() if r["active"]]
        covered = [r for r in active
                   if r["last_screened_day"] is not None
                   and r["last_screened_day"]
                   + (30 * (self.spec["interval_months_hiv"]
                            if r["hiv_positive"]
                            else self.spec["interval_months"])) >= as_of]
        return {"program": self.program, "eligible": len(active),
                "screened_in_interval": len(covered),
                "coverage_rate": (round(len(covered) / len(active), 3)
                                  if active else None),
                "never_screened": sum(
                    1 for r in active if r["last_screened_day"] is None),
                "lapsed_now": len(self.lapsed(as_of)),
                "register_version": CALL_RECALL_VERSION,
                "note": "the denominator is every active eligible woman "
                        "on the register — a rate without n is marketing"}

    def _r(self, pid):
        if pid not in self.enrolled:
            raise CallRecallError(f"not on the {self.program} register: "
                                  f"{pid}")
        return self.enrolled[pid]
