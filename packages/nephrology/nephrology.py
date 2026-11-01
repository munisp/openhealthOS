"""nephrology.py — CKD registry + dialysis unit governance.

Dialysis is where chronic care meets procedural risk: patients die when
sessions are missed, and dialysis units are documented HBV/HCV
transmission sites. So this module treats the unit like the platform
treats everything else:

  - eGFR staging fail-closed (KDIGO bands G1–G5; implausible refused)
  - missed sessions are escalations — absence is not health
  - water treatment (RO plant) tests expire; sessions on stale water are
    FLAGGED, never silently clean (calibration doctrine, applied to
    chloramine/endotoxin)
  - HBV+ patients run only on dedicated machines — segregation is
    enforced, not policy-papered
  - dialyzer reuse is capped and counted (reuse is economic reality in
    Nigeria; hiding it helps no one)
  - serology panels every 90 days; a seroconversion is an event with an
    isolation action, not a quiet lab result
  - catheter-days are counted (infection risk has a denominator)
  - adequacy (spKt/V) reports with denominators
  - transplant workup is a strictly sequential chain
"""

CKD_BANDS = ((90, "G1"), (60, "G2"), (45, "G3a"), (30, "G3b"),
             (15, "G4"), (0, "G5"))
WATER_TESTS = {"chloramine": 30, "endotoxin": 90}   # max days between tests
SEROLOGY_INTERVAL_DAYS = 90
SEROLOGY_PANEL = ("hbv", "hcv", "hiv")
DEFAULT_MAX_REUSE = 10
KTV_TARGET = 1.2
ACCESS_KINDS = ("avf", "avg", "catheter")
WORKUP_STAGES = ("referral", "labs", "imaging", "crossmatch", "listed")


class NephroError(Exception):
    pass


def ckd_stage(egfr):
    if egfr is None or not (1 <= egfr <= 150):
        raise NephroError(f"implausible eGFR: {egfr}")
    for floor, band in CKD_BANDS:
        if egfr >= floor:
            return band


class DialysisUnit:
    def __init__(self, facility, max_reuse=DEFAULT_MAX_REUSE):
        self.facility = facility
        self.max_reuse = max_reuse
        self.patients = {}
        self.sessions = {}
        self.water_log = []       # (kind, day, result)
        self.machines = {}        # machine_id -> {hbv_dedicated}
        self.dialyzers = {}       # dialyzer_id -> reuse count
        self.serology = {}        # patient_id -> {marker: (result, day)}
        self.events = []          # seroconversions, isolations
        self.access = {}          # patient_id -> access record
        self.workups = {}         # patient_id -> stage

    # ---- registry ----
    def register_patient(self, patient_id, *, dry_weight_kg, egfr, day,
                         sessions_per_week=3):
        if patient_id in self.patients:
            raise NephroError(f"duplicate patient: {patient_id}")
        if not (20 <= dry_weight_kg <= 250):
            raise NephroError("implausible dry weight")
        if not (1 <= sessions_per_week <= 7):
            raise NephroError("implausible session frequency")
        self.patients[patient_id] = {
            "dry_weight_kg": dry_weight_kg,
            "egfr": egfr, "ckd_stage": ckd_stage(egfr),
            "sessions_per_week": sessions_per_week, "registered_day": day,
            "last_session_day": None}
        return dict(self.patients[patient_id])

    def record_egfr(self, patient_id, egfr, *, day):
        p = self._patient(patient_id)
        p["egfr"], p["ckd_stage"] = egfr, ckd_stage(egfr)
        p["egfr_day"] = day
        return dict(p)

    # ---- water treatment: flagged, never faked ----
    def record_water_test(self, kind, *, day, result):
        if kind not in WATER_TESTS:
            raise NephroError(f"unknown water test: {kind}")
        if result not in ("pass", "fail"):
            raise NephroError("water test result must be pass/fail")
        self.water_log.append({"kind": kind, "day": day, "result": result})
        return dict(self.water_log[-1])

    def water_status(self, day):
        out = {}
        for kind, max_age in WATER_TESTS.items():
            tests = [t for t in self.water_log
                     if t["kind"] == kind and t["result"] == "pass"]
            if not tests:
                out[kind] = "never-tested"
            else:
                age = day - max(t["day"] for t in tests)
                out[kind] = ("verified" if age <= max_age
                             else f"stale-{age}d")
        return out

    # ---- machines & dialyzers ----
    def register_machine(self, machine_id, *, hbv_dedicated=False):
        if machine_id in self.machines:
            raise NephroError(f"duplicate machine: {machine_id}")
        self.machines[machine_id] = {"hbv_dedicated": bool(hbv_dedicated)}
        return dict(self.machines[machine_id])

    # ---- sessions ----
    def start_session(self, session_id, *, patient_id, machine_id,
                      dialyzer_id, day):
        p = self._patient(patient_id)
        if session_id in self.sessions:
            raise NephroError(f"duplicate session: {session_id}")
        if machine_id not in self.machines:
            raise NephroError(f"unknown machine: {machine_id}")
        flags = []
        # HBV segregation enforced, not policy-papered
        sero = self.serology.get(patient_id, {})
        hbv_pos = sero.get("hbv", ("unknown",))[0] == "positive"
        if hbv_pos and not self.machines[machine_id]["hbv_dedicated"]:
            raise NephroError(
                "HBV+ patient on a shared machine — refused; dedicated "
                "machine or no session")
        if not hbv_pos and self.machines[machine_id]["hbv_dedicated"]:
            flags.append("dedicated-machine-used-by-hbv-negative")
        # water staleness flags, never blocks care
        for kind, status in self.water_status(day).items():
            if status != "verified":
                flags.append(f"water-{kind}-{status}")
        # dialyzer reuse capped and counted
        uses = self.dialyzers.get(dialyzer_id, 0)
        if uses >= self.max_reuse:
            raise NephroError(
                f"dialyzer {dialyzer_id} at reuse cap "
                f"({self.max_reuse}) — retire it")
        self.dialyzers[dialyzer_id] = uses + 1
        self.sessions[session_id] = {
            "patient_id": patient_id, "machine_id": machine_id,
            "dialyzer_id": dialyzer_id, "dialyzer_use": uses + 1,
            "day": day, "flags": flags, "state": "running"}
        p["last_session_day"] = day
        return dict(self.sessions[session_id])

    def end_session(self, session_id, *, pre_weight, post_weight, day,
                    spktv=None):
        s = self._session(session_id)
        if s["state"] != "running":
            raise NephroError(f"session is {s['state']}")
        if post_weight > pre_weight:
            raise NephroError(
                "post-weight above pre-weight — ultrafiltration can't be "
                "negative; re-weigh, don't fudge")
        s.update({"state": "completed", "pre_weight": pre_weight,
                  "post_weight": post_weight, "spktv": spktv})
        return dict(s)

    def missed_sessions(self, as_of):
        """Absence is not health: a dialysis patient who misses sessions
        is an emergency, overdue-first."""
        out = []
        for pid, p in self.patients.items():
            gap_days = max(1, round(7 / p["sessions_per_week"]))
            last = p["last_session_day"]
            overdue = (as_of - last) if last is not None else (
                as_of - p["registered_day"])
            if overdue > gap_days:
                out.append({"patient_id": pid,
                            "days_since_session": overdue,
                            "never_dialyzed": last is None})
        return sorted(out, key=lambda x: -x["days_since_session"])

    # ---- serology & seroconversion ----
    def record_serology(self, patient_id, *, results, day):
        unknown = set(results) - set(SEROLOGY_PANEL)
        if unknown:
            raise NephroError(f"unknown markers: {sorted(unknown)}")
        self._patient(patient_id)
        cur = self.serology.setdefault(patient_id, {})
        for marker, result in results.items():
            if result not in ("positive", "negative"):
                raise NephroError("serology must be positive/negative")
            prev = cur.get(marker, ("negative",))[0]
            cur[marker] = (result, day)
            if prev == "negative" and result == "positive":
                self.events.append({
                    "patient_id": patient_id, "day": day,
                    "event": f"seroconversion-{marker}",
                    "action": "isolate + dedicated machine + outbreak "
                              "review — a seroconversion in a dialysis "
                              "unit is a unit-level finding"})
        return dict(cur)

    def serology_due(self, as_of):
        out = []
        for pid in self.patients:
            cur = self.serology.get(pid, {})
            stale = [m for m in SEROLOGY_PANEL
                     if m not in cur
                     or as_of - cur[m][1] > SEROLOGY_INTERVAL_DAYS]
            if stale:
                out.append({"patient_id": pid, "due": stale})
        return out

    # ---- vascular access ----
    def register_access(self, patient_id, *, kind, day):
        if kind not in ACCESS_KINDS:
            raise NephroError(f"unknown access kind: {kind}")
        self._patient(patient_id)
        self.access[patient_id] = {"kind": kind, "created_day": day,
                                   "failed": None}
        return dict(self.access[patient_id])

    def fail_access(self, patient_id, *, day, reason):
        a = self.access.get(patient_id)
        if not a:
            raise NephroError("no access registered")
        if not reason:
            raise NephroError("access failure requires a reason")
        a["failed"] = {"day": day, "reason": reason}
        return dict(a)

    def catheter_days(self, as_of):
        """Catheter-days = the infection-risk denominator."""
        days = 0
        for a in self.access.values():
            if a["kind"] == "catheter":
                end = a["failed"]["day"] if a["failed"] else as_of
                days += max(0, end - a["created_day"])
        return {"catheter_days": days,
                "note": "infection rates divide by this, never by "
                        "patient count"}

    # ---- adequacy ----
    def adequacy_report(self):
        ktvs = [s["spktv"] for s in self.sessions.values()
                if s.get("spktv") is not None]
        n = len(ktvs)
        adequate = sum(1 for k in ktvs if k >= KTV_TARGET)
        return {"sessions_with_ktv": n, "adequate": adequate,
                "target": KTV_TARGET,
                "note": "adequacy = sessions >=1.2 / sessions measured; "
                        "unmeasured sessions are not assumed adequate"}

    # ---- transplant workup: strictly sequential ----
    def advance_workup(self, patient_id, stage, *, day, by):
        if stage not in WORKUP_STAGES:
            raise NephroError(f"unknown workup stage: {stage}")
        cur = self.workups.get(patient_id)
        if cur is None:
            if stage != "referral":
                raise NephroError("workup starts at referral")
            self.workups[patient_id] = {"stage": stage, "history":
                                        [(stage, day, by)]}
            return dict(self.workups[patient_id])
        idx = WORKUP_STAGES.index(cur["stage"])
        if WORKUP_STAGES.index(stage) != idx + 1:
            raise NephroError(
                f"workup is sequential: {cur['stage']} → {stage} skips "
                "steps")
        cur["stage"] = stage
        cur["history"].append((stage, day, by))
        return dict(cur)

    def _patient(self, pid):
        if pid not in self.patients:
            raise NephroError(f"unknown patient: {pid}")
        return self.patients[pid]

    def _session(self, sid):
        if sid not in self.sessions:
            raise NephroError(f"unknown session: {sid}")
        return self.sessions[sid]
