"""crosslink.py — the wiring layer: engines compose into one platform.

Each seam below joins two packages that previously stood alone. Every
seam is fail-closed, versioned where it carries clinical rules, and
shaped so that silence is a finding, never a default.

    1. renal_check        nephrology eGFR  → pharmacy orders
    2. biopsy_to_path     telederm biopsy  → telepath specimen custody
    3. case_pack          systems of record → MDT evidence refs
    4. cryo_watch         coldchain drift math → fertility cryo tanks
    5. chair_plan         scheduling demand  → dialysis chairs (HBV-safe)
    6. on_shift_readers   rota             → teleread candidate pool
    7. escalate_events    trigger events   → tele-ICU oversight
    8. timeouts_to_review steward timeout  → pharmacy review queue
    9. deterioration_to_icu NEWS2 trend   → tele-ICU oversight
   10. order_with_priors  pacs priors     → teleread worklist item

Nothing here re-implements clinical logic; it composes engines that
already refuse loudly on their own. The seams add the one thing the
engines could not know alone: that the OTHER system exists.
"""


class CrosslinkError(Exception):
    pass


# ---------------------------------------------------------------- 1.
# nephrology → pharmacy: the latest eGFR meets the drug order.
#
# Rules are DATA, versioned — a dosing rule without a version is an
# unaccountable intervention. The table's ONE home is the formulary
# package (drug knowledge lives with drug knowledge); crosslink
# re-exports it here so the seam keeps its import surface.
from drugkb import (RENAL_RULES, RENAL_RULES_VERSION,  # noqa: E402,F401
                    EGFR_STALE_DAYS, renal_verdict)


def renal_check(unit, patient_id, drug, *, day):
    """Latest eGFR from the nephrology registry meets a drug order.

    Refuses when the patient has no renal function on record — absence
    of an eGFR is not renal function. An unrated drug is NOT 'clear':
    absence of a rule is not clearance.
    """
    if patient_id not in unit.patients:
        raise CrosslinkError(
            f"no renal function on record for {patient_id} — "
            "register and measure before dosing, not after")
    p = unit.patients[patient_id]
    egfr = p["egfr"]
    flags = []
    egfr_day = p.get("egfr_day", p.get("registered_day"))
    if egfr_day is not None and day - egfr_day > EGFR_STALE_DAYS:
        flags.append(f"egfr-stale-{day - egfr_day}d — recheck before "
                     "trusting this verdict")

    # the formulary resolves drug AND class (ibuprofen → nsaid);
    # most-restrictive match wins
    rv = renal_verdict(drug, egfr)
    if rv is None:
        return {"patient_id": patient_id, "drug": drug, "egfr": egfr,
                "ckd_stage": p["ckd_stage"], "verdict": "unrated",
                "flags": flags, "rule_version": RENAL_RULES_VERSION,
                "note": "absence of a rule is not clearance — route to "
                        "pharmacist review"}
    out = {"patient_id": patient_id, "drug": drug, "egfr": egfr,
           "ckd_stage": p["ckd_stage"], "verdict": rv["verdict"],
           "flags": flags, "rule_version": rv["rule_version"],
           "matched": rv["matched"]}
    if rv["verdict"] != "clear":
        out["reason"] = rv["note"]
    return out


# ---------------------------------------------------------------- 2.
# telederm → telepath: 'biopsy this' becomes a specimen in custody.
def biopsy_to_path(derm, path, derm_case_id, specimen_id, *, day,
                   collector, facility):
    """A dermatologist's biopsy decision becomes a registered pathology
    specimen with the site pre-filled from the derm case — an unsited
    biopsy is an uninterpretable one, and a biopsy nobody registered is
    a patient who never learns their diagnosis.

    The seam ends at custody start: fixation, processing and scanning
    are real-world steps and are NOT faked here.
    """
    if derm_case_id not in derm.lesions:
        raise CrosslinkError(
            f"unknown derm case: {derm_case_id} — a biopsy without a "
            "source case is a specimen without a question")
    lesion = derm.lesions[derm_case_id]
    spec = path.register_specimen(
        specimen_id, patient_id=lesion["patient_id"],
        specimen_type="biopsy", site=lesion["body_site"],
        collector=collector, facility=facility, day=day)
    return {"specimen": spec, "derm_case_id": derm_case_id,
            "next": "custody chain: fixed → processed → sectioned → "
                    "stained → scanned, then submit_for_reading",
            "note": "the reading queue opens only after 'scanned' — "
                    "this seam does not skip physics"}


# ---------------------------------------------------------------- 3.
# systems of record → MDT: the case pack is PULLED, never typed.
def case_pack(patient_id, *, case_ids=(), rk=None, path=None,
              pacs_svc=None):
    """Assemble MDT evidence from the systems of record.

    Only FINAL evidence enters the pack: signed reports, read-ready
    (scanned) specimens, acquired studies. Drafts and mid-custody
    specimens are listed as findings — an MDT decides from verified
    reports, not from works in progress.
    """
    refs, findings = [], []
    case_ids = set(case_ids)

    if rk is not None:
        for rid, r in sorted(rk.reports.items()):
            if r["case_id"] in case_ids:
                if r["status"] == "signed":
                    h = r["signature"]["content_hash"][:16]
                    refs.append(f"report:{rid}#{h}")
                else:
                    findings.append(f"report {rid} is a draft — not "
                                    "evidence until signed")
    if path is not None:
        for sid, s in sorted(path.specimens.items()):
            if s["patient_id"] == patient_id:
                if s["state"] == "scanned":
                    refs.append(f"specimen:{sid}@{s['state']}")
                else:
                    findings.append(f"specimen {sid} is '{s['state']}' "
                                    "— mid-custody, not yet evidence")
    if pacs_svc is not None:
        for uid, st in sorted(pacs_svc.studies.items()):
            if st["patient_id"] == patient_id:
                refs.append(f"study:{uid}")

    return {"patient_id": patient_id, "evidence_refs": refs,
            "findings": findings,
            "note": "refs are pulled from the systems of record and "
                    "carry their version/hash — never typed from memory"}


def submit_with_pack(board, case_id, *, patient_id, question, pack,
                     submitted_by, day):
    """Submit an MDT case whose evidence is a pulled pack. Empty packs
    refuse here (loudly, with findings) before the board is touched."""
    if not pack["evidence_refs"]:
        raise CrosslinkError(
            "empty case pack — an MDT without evidence is a meeting "
            f"about vibes; findings: {pack['findings'] or 'none'}")
    return board.submit_case(case_id, patient_id=patient_id,
                             question=question,
                             evidence_refs=pack["evidence_refs"],
                             submitted_by=submitted_by, day=day)


# ---------------------------------------------------------------- 4.
# coldchain → fertility cryo: a cryo ticket names WHOSE embryos warm.
#
# LN2 vapour-phase storage must stay at/below -150 °C (glass transition
# of water is ≈ -132 °C; -150 gives margin). The excursion/drift math
# mirrors coldchain.analyze; the band and the stake are cryo's own.
CRYO_ALARM_C = -150.0
CRYO_FLOOR_C = -210.0          # colder than LN2 liquid = broken sensor
CRYO_MIN_READINGS = 4          # abstain below this, like coldchain
CRYO_HORIZON_H = 24.0          # cryo failure is fast; 3 days is fiction


def _slope_per_hour(readings):
    """Least-squares °C/hour — same math as coldchain._slope_per_hour,
    duplicated deliberately: packages compose through public seams,
    not private helpers."""
    from datetime import datetime

    def dt(ts):
        return datetime.fromisoformat(
            ts.replace("Z", "+00:00")).replace(tzinfo=None)

    t0 = dt(readings[0]["ts"])
    xs = [(dt(r["ts"]) - t0).total_seconds() / 3600 for r in readings]
    ys = [r["celsius"] for r in readings]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom


def cryo_watch(clinic, tank_id, readings):
    """One cryo tank, one decision — and the ticket names the embryos.

    Abstains (honestly) below CRYO_MIN_READINGS. Refuses physically
    impossible readings. On excursion or predicted breach, the ticket
    lists every frozen embryo in the tank with its couple — a fridge
    ticket says 'warm'; a cryo ticket says WHO is warming.
    """
    rs = sorted(readings, key=lambda r: r["ts"])
    if len(rs) < CRYO_MIN_READINGS:
        return {"tank_id": tank_id, "ticket": None,
                "abstained": f"{len(rs)} readings < "
                             f"{CRYO_MIN_READINGS} — more data, "
                             "not more confidence"}
    last = rs[-1]
    if last["celsius"] < CRYO_FLOOR_C:
        raise CrosslinkError(
            f"{last['celsius']}°C is colder than liquid nitrogen — "
            "a broken sensor, not a cold tank; refuse, recalibrate")

    at_risk = [{"embryo_id": eid, "couple_id": e["couple_id"],
                "canister": e["location"]["canister"],
                "position": e["location"]["position"]}
               for eid, e in sorted(clinic.embryos.items())
               if e["stage"] == "frozen" and e["location"]
               and e["location"]["tank"] == tank_id]

    def ticket(priority, reason):
        return {"tank_id": tank_id, "priority": priority,
                "reason": reason, "current_celsius": last["celsius"],
                "embryos_at_risk": at_risk,
                "note": "a coldchain ticket says a fridge is warm; a "
                        "cryo ticket says WHOSE embryos are warming"}

    if last["celsius"] > CRYO_ALARM_C:
        return {"tank_id": tank_id,
                "ticket": ticket("P1", f"EXCURSION {last['celsius']:.0f}°C "
                                       f"above {CRYO_ALARM_C:.0f}°C — "
                                       "embryo viability window is hours")}
    slope = _slope_per_hour(rs)
    if slope > 0.05:  # warming toward the alarm line
        hours = (CRYO_ALARM_C - last["celsius"]) / slope
        if 0 < hours <= CRYO_HORIZON_H:
            return {"tank_id": tank_id,
                    "ticket": ticket("P2", f"DRIFT +{slope:.2f}°C/h — "
                                           f"crosses {CRYO_ALARM_C:.0f}°C "
                                           f"in ~{hours:.0f}h; move "
                                           "canisters before the breach")}
    return {"tank_id": tank_id, "ticket": None,
            "note": "within band; the watch continues"}


# ---------------------------------------------------------------- 5.
# scheduling → dialysis chairs: the day's demand meets real machines.
def chair_plan(unit, *, day, shifts=("morning", "afternoon")):
    """Which patient takes which machine in which shift.

    HBV+ patients are planned onto hbv_dedicated machines ONLY (the
    session engine refuses otherwise — the plan must not promise what
    the unit will refuse). HBV-negative patients keep off dedicated
    machines unless nothing else exists, and then it is a FLAG, matching
    start_session. A due patient with no chair is a loud waitlist entry,
    never a quiet dropout.
    """
    if not unit.machines:
        raise CrosslinkError(
            "a chair plan over zero machines is fiction — register "
            "machines first")
    due = []
    for pid, p in sorted(unit.patients.items()):
        interval = max(1, 7 // p["sessions_per_week"])
        last = p["last_session_day"]
        if last is None or day - last >= interval:
            sero = unit.serology.get(pid, {})
            hbv = sero.get("hbv", ("unknown",))[0] == "positive"
            due.append({"patient_id": pid, "hbv": hbv})
    # scarcer resource first: HBV+ patients have fewer legal chairs
    due.sort(key=lambda d: (not d["hbv"], d["patient_id"]))

    dedicated = sorted(m for m, v in unit.machines.items()
                       if v["hbv_dedicated"])
    shared = sorted(m for m, v in unit.machines.items()
                    if not v["hbv_dedicated"])
    plan = {s: [] for s in shifts}
    waitlist = []
    occupied = set()  # (shift, machine)

    def place(d, pool, shift):
        for m in pool:
            if (shift, m) not in occupied:
                return m
        return None

    for d in due:
        placed = False
        for shift in shifts:
            if d["hbv"]:
                m = place(d, dedicated, shift)
                flags = []
            else:
                m = place(d, shared, shift)
                flags = []
                if m is None:  # overflow onto dedicated: flag, as the engine does
                    m = place(d, dedicated, shift)
                    if m:
                        flags.append("dedicated-machine-used-by-hbv-negative")
            if m:
                plan[shift].append({"patient_id": d["patient_id"],
                                    "machine_id": m, "flags": flags})
                occupied.add((shift, m))
                placed = True
                break
        if not placed:
            waitlist.append(d["patient_id"])
    return {"day": day, "shifts": plan, "waitlist": waitlist,
            "note": "the waitlist is the gap made loud — a due patient "
                    "with no chair is a capacity finding, not a no-show"}


# ---------------------------------------------------------------- 6.
# rota → teleread: off-duty is not a candidate pool.
def on_shift_readers(rota, *, day):
    """Staff actually rostered today become the assignment candidate
    pool. teleread still applies credential + scope + licence-expiry
    filters on top; when nobody on shift is credentialed the case
    waits, loudly — it does not fall to the unlicensed, and it does
    not fall on the reader who went home."""
    on = sorted({sid for sh in rota.shifts.values() if sh.day == day
                 for sid in sh.assigned})
    return {"day": day, "candidates": on,
            "note": "off-duty is not a candidate pool; rest is a "
                    "patient-safety feature"}


def assign_on_shift(reading_svc, case_id, *, day, pool):
    """Assign a reading case from the rota pool — and ONLY the pool.

    teleread's assign treats candidates=[] as 'no preference' and
    re-widens to every reader (an empty list is falsy). That is the
    wrong semantics for a duty roster: nobody-on-shift must REFUSE,
    not silently page the reader who went home.
    """
    if not pool["candidates"]:
        raise CrosslinkError(
            f"nobody rostered on {pool['day']} — the case waits for a "
            "rostered, credentialed reader; it does not hunt the "
            "off-duty")
    return reading_svc.assign(case_id, day=day,
                              candidates=pool["candidates"])


# ---------------------------------------------------------------- 7.
# triggers → tele-ICU: an unacknowledged alarm gets a doctor.
SEVERITY_TO_PRIORITY = {"critical": "stat", "high": "urgent"}


def escalate_events(engine, icu, *, facility, day, opened):
    """Unacknowledged critical/high trigger events open tele-ICU
    oversight. `opened` is a caller-owned dict (event_id → case_id) so
    repeat calls are idempotent — the second bell is not a second case.

    Moderate events stay local: tele-ICU is scarce; alert fatigue is
    how real alarms die.
    """
    opened_now, already = [], []
    for ev in engine.unacknowledged(day):
        prio = SEVERITY_TO_PRIORITY.get(ev["severity"])
        if prio is None:
            continue
        if ev["id"] in opened:
            already.append(opened[ev["id"]])
            continue
        case_id = f"icu-{ev['id']}"
        icu.open_oversight(case_id, patient_id=ev["patient_id"],
                           facility=facility, trigger_ref=ev["id"],
                           day=day, severity=prio)
        opened[ev["id"]] = case_id
        opened_now.append(case_id)
    return {"opened": opened_now, "already_open": already,
            "note": "an unacknowledged alarm gets a doctor, not just a "
                    "louder alarm"}


# ---------------------------------------------------------------- 8.
# steward → pharmreview: the 48h antibiotic timeout joins the queue.
def timeouts_to_review(pharm, clock, *, day):
    """Stewardship timeouts become pharmacist review cases. The clock
    decides WHAT is due; pharmreview decides WHO reviews. Idempotent
    per due-day (pharmreview's queue_timeout owns the dedup) — the
    second ring of the bell is not a second case."""
    due = clock.timeouts_due(day)
    queued = [pharm.queue_timeout(c, day=day) for c in due]
    return {"due": len(due),
            "newly_queued": sum(1 for q in queued
                                if not q.get("already_queued")),
            "case_ids": [q["case_id"] for q in queued],
            "note": "an antibiotic past its timeout gets a pharmacist, "
                    "not a shrug"}


# ---------------------------------------------------------------- 9.
# earlywarn → tele-ICU: a deteriorating trajectory gets oversight.
def deterioration_to_icu(warner, icu, *, facility, day, threshold=5,
                         opened):
    """NEWS2 deterioration (score ≥ threshold, or a band climbing)
    opens a tele-ICU oversight case. Idempotent per patient via the
    caller-owned `opened` map — the trajectory may keep rising; the
    case opens once."""
    opened_now, already = [], []
    for p in warner.deteriorating(day, threshold=threshold):
        pid = p["patient_id"]
        if pid in opened:
            already.append(opened[pid])
            continue
        case_id = f"icu-news2-{pid}"
        icu.open_oversight(
            case_id, patient_id=pid, facility=facility,
            trigger_ref=f"news2:{p['score']}:{p['band']}", day=day,
            severity="stat" if p["band"] == "high" else "urgent")
        opened[pid] = case_id
        opened_now.append(case_id)
    return {"opened": opened_now, "already_open": already,
            "note": "the trajectory is the finding — a rising score "
                    "gets a doctor, not a larger font"}


# ---------------------------------------------------------------- 10.
# pacs → teleread: priors ride the worklist item from birth.
def order_with_priors(reading_svc, pacs_svc, case_id, *, patient_id,
                      modality=None, **order_kwargs):
    """Order a read with the patient's priors chain attached at ORDER
    time — not when the reader remembers to ask. A worklist item that
    hides the priors sends the reader in with one eye closed; zero
    priors is stated honestly, never silently absent."""
    from serial import priors
    chain = priors(pacs_svc, patient_id, modality=modality)
    meta = dict(order_kwargs.pop("meta", None) or {})
    meta["patient_id"] = patient_id
    meta["priors"] = {"count": chain["count"],
                      "study_uids": [s["study_uid"]
                                     for s in chain["studies"]],
                      "pulled_at_order": True}
    out = reading_svc.order(case_id, meta=meta, **order_kwargs)
    return dict(out, priors=chain)
