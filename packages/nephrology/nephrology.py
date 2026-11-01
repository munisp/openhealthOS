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
  - per-session prescriptions are versioned records (UF target, flows,
    dialysate composition, heparin, sodium profile) — a change is a new
    version citing the old, never an edit
  - intradialytic events (hypotension, cramps, clotting) carry the
    action taken — an event without a response is a monitor log,
    not care
  - anemia: iron before ESA, ESA refused above Hb 11.5, dose changes
    bounded per month; the protocol DRAFTS, the nephrologist signs
  - CKD-MBD: Ca/PO4/PTH trends with binder/vitamin-D review drafts
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

# ---- per-session prescription bounds (KDIGO/KDOQI-shaped, deployment-
# tunable; every field fail-closed against plausibility) ----
PRESCRIPTION_FIELDS = {
    "target_uf_ml": (0, 8000),
    "blood_flow_ml_min": (100, 500),
    "dialysate_flow_ml_min": (300, 800),
    "dialysate_sodium_mmol": (130, 155),
    "dialysate_potassium_mmol": (0, 4),
    "dialysate_calcium_mmol": (1.0, 2.0),
    "dialysate_bicarbonate_mmol": (25, 40),
    "heparin_bolus_iu": (0, 10000),
    "heparin_infusion_iu_h": (0, 3000),
    "duration_min": (60, 480),
}
SODIUM_PROFILES = ("constant", "step", "linear")
PRESCRIPTION_REQUIRED = ("target_uf_ml", "blood_flow_ml_min",
                         "duration_min")
INTRADIALYTIC_EVENTS = ("hypotension", "cramps", "clotting",
                        "arrhythmia", "access-issue", "pyrogenic")

# ---- anemia (KDIGO-shaped): iron first, ESA cautiously, never above
# Hb 11.5 — the trials were unambiguous about harm up there ----
HB_TARGET_LO, HB_TARGET_HI = 10.0, 11.5
HB_NO_ESA_ABOVE = 11.5
TSAT_IRON_FIRST = 20.0        # % below → iron before ESA
FERRITIN_IRON_FIRST = 200.0   # ng/mL below → iron before ESA
ESA_MAX_MONTHLY_CHANGE_PCT = 25
HB_FAST_RISE = 1.0            # g/dL per 2 weeks → reduce dose

# ---- CKD-MBD plausibility bounds ----
MBD_BOUNDS = {"calcium_mmol": (1.0, 4.0), "phosphate_mmol": (0.3, 6.0),
              "pth_pmol_l": (1.0, 300.0)}
PHOSPHATE_HIGH = 1.45         # mmol/L — binder review above
PTH_HIGH = 60.0               # pmol/L ≈ 570 pg/mL — vitamin-D review


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
        self.prescriptions = {}   # patient_id -> [versioned rx]
        self.anemia = {}          # patient_id -> [lab series]
        self.esa_doses = []       # append-only administrations
        self.mbd = {}             # patient_id -> [lab series]

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
                      dialyzer_id, day, prescription_version=None):
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
        # prescription binding: named rx or a loud flag
        rxs = self.prescriptions.get(patient_id, [])
        if prescription_version is not None:
            if not any(r["version"] == prescription_version
                       for r in rxs):
                raise NephroError(
                    f"no prescription v{prescription_version} for "
                    f"{patient_id} — binding to a ghost prescription "
                    "is how the wrong settings reach the machine")
        elif rxs:
            flags.append("no-prescription-bound")
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
            "prescription_version": prescription_version,
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

    # ---- prescriptions: versioned, never edited ----
    def set_prescription(self, patient_id, *, day, by,
                         sodium_profile="constant", **fields):
        """A dialysis prescription is the machine's orders. Changes are
        new versions citing the old — because intradialytic hypotension
        needs an audit trail back to what the machine was TOLD to do,
        not what someone now remembers telling it."""
        self._patient(patient_id)
        if sodium_profile not in SODIUM_PROFILES:
            raise NephroError(f"unknown sodium profile: {sodium_profile}")
        unknown = set(fields) - set(PRESCRIPTION_FIELDS)
        if unknown:
            raise NephroError(
                f"unknown prescription fields: {sorted(unknown)}")
        missing = [k for k in PRESCRIPTION_REQUIRED if k not in fields]
        if missing:
            raise NephroError(
                f"prescription missing {missing} — an incomplete "
                "prescription is an unsafe one")
        for k, v in fields.items():
            lo, hi = PRESCRIPTION_FIELDS[k]
            if not (lo <= v <= hi):
                raise NephroError(
                    f"implausible {k}: {v} (plausible {lo}-{hi}) — "
                    "refused, not clamped")
        rxs = self.prescriptions.setdefault(patient_id, [])
        rx = {"version": len(rxs) + 1, "day": day, "by": by,
              "sodium_profile": sodium_profile, "fields": dict(fields),
              "supersedes": rxs[-1]["version"] if rxs else None}
        rxs.append(rx)
        return dict(rx)

    def current_prescription(self, patient_id):
        rxs = self.prescriptions.get(patient_id)
        if not rxs:
            raise NephroError(
                f"no prescription for {patient_id} — dialyzing without "
                "one is flying the machine by memory")
        return dict(rxs[-1])

    # ---- intradialytic events: the response is part of the event ----
    def record_intradialytic(self, session_id, *, event, minute,
                             action, day, by):
        s = self._session(session_id)
        if s["state"] != "running":
            raise NephroError(
                f"session is {s['state']} — events belong to a running "
                "session, not a reconstructed one")
        if event not in INTRADIALYTIC_EVENTS:
            raise NephroError(f"unknown intradialytic event: {event}")
        if not action:
            raise NephroError(
                "an intradialytic event without an action is a monitor "
                "log, not care — what was DONE?")
        if not (0 <= minute <= s.get("duration_min", 480)):
            raise NephroError("implausible event minute")
        ev = {"event": event, "minute": minute, "action": action,
              "day": day, "by": by}
        s.setdefault("events", []).append(ev)
        return dict(ev)

    def intradialytic_report(self, patient_id=None):
        """Hypotension and friends, with denominators."""
        sessions = [s for s in self.sessions.values()
                    if patient_id is None
                    or s["patient_id"] == patient_id]
        n = len(sessions)
        counts = {e: 0 for e in INTRADIALYTIC_EVENTS}
        for s in sessions:
            for ev in s.get("events", []):
                counts[ev["event"]] += 1
        return {"sessions": n, "events": counts,
                "hypotension_per_100_sessions": (
                    round(100 * counts["hypotension"] / n, 1)
                    if n else None),
                "note": "rates divide by sessions run; uneventful "
                        "sessions are the denominator, not an omission"}

    # ---- anemia: the protocol drafts, the nephrologist signs ----
    def record_anemia_labs(self, patient_id, *, hb_g_dl, tsat_pct,
                           ferritin_ng_ml, day):
        self._patient(patient_id)
        if not (3.0 <= hb_g_dl <= 20.0):
            raise NephroError(f"implausible Hb: {hb_g_dl}")
        if not (1.0 <= tsat_pct <= 80.0):
            raise NephroError(f"implausible TSAT: {tsat_pct}")
        if not (10.0 <= ferritin_ng_ml <= 5000.0):
            raise NephroError(f"implausible ferritin: {ferritin_ng_ml}")
        series = self.anemia.setdefault(patient_id, [])
        entry = {"day": day, "hb": hb_g_dl, "tsat": tsat_pct,
                 "ferritin": ferritin_ng_ml}
        series.append(entry)
        return dict(entry)

    def anemia_review(self, patient_id, *, day):
        """Protocol-driven DRAFT. Iron repletion precedes ESA; ESA is
        refused above the harm threshold; fast rises force reduction.
        Nothing here administers anything."""
        series = self.anemia.get(patient_id)
        if not series:
            raise NephroError(
                f"no anemia labs for {patient_id} — reviewing without "
                "labs is prescribing from vibes")
        latest = series[-1]
        hb, tsat, fer = latest["hb"], latest["tsat"], latest["ferritin"]
        current_esa = self._current_esa(patient_id)

        rec = {"patient_id": patient_id, "day": day, "labs": dict(latest),
               "labs_age_days": day - latest["day"],
               "current_esa_iu_wk": current_esa,
               "recommendation": None, "draft": True,
               "note": "a protocol drafts; the nephrologist signs"}
        if hb > HB_NO_ESA_ABOVE:
            rec["recommendation"] = {
                "action": "hold-esa",
                "reason": f"Hb {hb} > {HB_NO_ESA_ABOVE} g/dL — ESA "
                          "above this is harm, not care (TREAT/CHOIR)"}
            return rec
        if tsat < TSAT_IRON_FIRST or fer < FERRITIN_IRON_FIRST:
            rec["recommendation"] = {
                "action": "iv-iron-first",
                "reason": f"TSAT {tsat}% / ferritin {fer} — iron "
                          "repletion before any ESA change; ESA on "
                          "empty iron stores buys strokes, not Hb"}
            return rec
        if len(series) >= 2:
            prev = series[-2]
            weeks = max(1, (latest["day"] - prev["day"]) / 7)
            rise_rate = (hb - prev["hb"]) / weeks
            if rise_rate > HB_FAST_RISE / 2 and current_esa:
                rec["recommendation"] = {
                    "action": "reduce-esa",
                    "max_change_pct": -ESA_MAX_MONTHLY_CHANGE_PCT,
                    "reason": f"Hb rising {rise_rate:.2f} g/dL/wk — "
                              "fast rises are the thrombosis signal; "
                              "reduce, don't celebrate"}
                return rec
        if hb < HB_TARGET_LO and not current_esa:
            rec["recommendation"] = {
                "action": "start-esa",
                "suggested_iu_wk": 4000,
                "reason": f"Hb {hb} < {HB_TARGET_LO} with replete iron "
                          "— ESA indicated at conservative start dose"}
        elif hb < HB_TARGET_LO:
            rec["recommendation"] = {
                "action": "titrate-esa",
                "max_change_pct": ESA_MAX_MONTHLY_CHANGE_PCT,
                "reason": f"Hb {hb} below target on ESA — titrate "
                          "within the monthly bound"}
        else:
            rec["recommendation"] = {
                "action": "maintain",
                "reason": f"Hb {hb} in target "
                          f"{HB_TARGET_LO}-{HB_TARGET_HI}"}
        return rec

    def administer_esa(self, patient_id, *, dose_iu, day, by,
                       review_day):
        """An administration must cite a review — the draft-then-sign
        loop applied to drugs. ESA above the harm threshold refuses."""
        series = self.anemia.get(patient_id)
        if not series:
            raise NephroError("no anemia labs — refusing blind ESA")
        if series[-1]["hb"] > HB_NO_ESA_ABOVE:
            raise NephroError(
                f"Hb {series[-1]['hb']} > {HB_NO_ESA_ABOVE} — ESA "
                "refused at any dose; the trials were unambiguous")
        if not (500 <= dose_iu <= 30000):
            raise NephroError(f"implausible ESA dose: {dose_iu}")
        if review_day > day:
            raise NephroError("the review cannot postdate the dose")
        entry = {"patient_id": patient_id, "dose_iu": dose_iu,
                 "day": day, "by": by, "review_day": review_day}
        self.esa_doses.append(entry)
        return dict(entry)

    def esa_response_report(self):
        on_esa = {e["patient_id"] for e in self.esa_doses}
        in_target = sum(
            1 for pid in on_esa
            if self.anemia.get(pid)
            and HB_TARGET_LO <= self.anemia[pid][-1]["hb"]
            <= HB_TARGET_HI)
        return {"patients_on_esa": len(on_esa),
                "hb_in_target": in_target,
                "note": "target 10-11.5 g/dL; the denominator is "
                        "patients on ESA, not the whole unit"}

    def _current_esa(self, patient_id):
        doses = [e for e in self.esa_doses
                 if e["patient_id"] == patient_id]
        return doses[-1]["dose_iu"] if doses else 0

    # ---- CKD-MBD: trends with review drafts ----
    def record_mbd_labs(self, patient_id, *, calcium_mmol,
                        phosphate_mmol, pth_pmol_l, day):
        self._patient(patient_id)
        vals = {"calcium_mmol": calcium_mmol,
                "phosphate_mmol": phosphate_mmol,
                "pth_pmol_l": pth_pmol_l}
        for k, v in vals.items():
            lo, hi = MBD_BOUNDS[k]
            if not (lo <= v <= hi):
                raise NephroError(f"implausible {k}: {v}")
        series = self.mbd.setdefault(patient_id, [])
        series.append({"day": day, **vals})
        return dict(series[-1])

    def mbd_review(self, patient_id, *, day):
        series = self.mbd.get(patient_id)
        if not series:
            raise NephroError(f"no CKD-MBD labs for {patient_id}")
        latest = series[-1]
        drafts = []
        if latest["phosphate_mmol"] > PHOSPHATE_HIGH:
            drafts.append({"draft": "binder-review",
                           "reason": f"phosphate "
                                     f"{latest['phosphate_mmol']} > "
                                     f"{PHOSPHATE_HIGH} mmol/L — "
                                     "adherence, diet, binder class"})
        if latest["pth_pmol_l"] > PTH_HIGH:
            drafts.append({"draft": "vitamin-d-review",
                           "reason": f"PTH {latest['pth_pmol_l']} > "
                                     f"{PTH_HIGH} pmol/L — secondary "
                                     "hyperparathyroidism workup"})
        if len(series) >= 2 and \
                latest["pth_pmol_l"] > series[-2]["pth_pmol_l"]:
            drafts.append({"draft": "pth-trend-rising",
                           "reason": "PTH rising across draws — trend "
                                     "is the diagnosis, not the "
                                     "snapshot"})
        return {"patient_id": patient_id, "day": day,
                "labs": dict(latest), "drafts": drafts,
                "note": "drafts, not orders — the nephrologist decides"}

    def _patient(self, pid):
        if pid not in self.patients:
            raise NephroError(f"unknown patient: {pid}")
        return self.patients[pid]

    def _session(self, sid):
        if sid not in self.sessions:
            raise NephroError(f"unknown session: {sid}")
        return self.sessions[sid]
