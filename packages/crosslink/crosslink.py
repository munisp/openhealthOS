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
   11..15 (see package history)      …      screens, synoptic, MDT→orders
   16. verify_with_pixels viewerlaunch   → teleread verify gate
   17. imported_case_signal travelpass   → surveillance syndromes
   18. poe_entry_screen   poe_screen     → travelpass pass record
   19. feed_health_to_oversight devicelink → teleicu oversight
   20. deid_export_to_dataset pacs de-id  → datasetforge cases
   21. emergency_to_claim   travelpass    → vhclaims intake
   22. premium_to_fund      vhcover        → vhfund buckets

Nothing here re-implements clinical logic; it composes engines that
already refuse loudly on their own. The seams add the one thing the
engines could not know alone: that the OTHER system exists.
"""


import os
import sys


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


# ---------------------------------------------------------------- 11.
# cervixscreen → callrecall: a screen closes the register's round.
def screens_to_register(cervix, register, *, day, marked):
    """Every woman the screening module has seen closes her invitation
    round on the call/recall register — automatically, so the register
    keeps inviting only the women who have NOT come. Idempotent via
    the caller-owned `marked` map: the second ring of the bell is not
    a second case."""
    marked_now, already = [], []
    for pid, c in cervix.cascade.items():
        if pid in marked:
            already.append(pid)
            continue
        try:
            register.mark_screened(pid, day=c["day"])
        except Exception:
            continue   # not on this register — screening out-of-cohort
                       # is care, not an error; the register only tracks
                       # its own
        marked[pid] = c["day"]
        marked_now.append(pid)
    return {"marked_screened": marked_now, "already_marked": already,
            "note": "the register exists to find the women who did NOT "
                    "come — a screen must stop the invitations"}


# ---------------------------------------------------------------- 12.
# cervixscreen → telepath: the colposcopy biopsy enters custody.
def colpo_biopsy_to_path(cervix, path, patient_id, *, specimen_id,
                         collector, day):
    """A colposcopy decision of 'biopsy' opens TWO things: the
    histology loop on the screening cascade, and a specimen in the
    pathology custody chain. Both, or neither — a biopsy in a coolbox
    with no custody record is a woman the programme is about to lose.
    Site is fixed: cervix."""
    c = cervix.cascade.get(patient_id)
    if not c:
        raise CrosslinkError(f"unknown screening patient: {patient_id}")
    if not c["histology_pending"]:
        raise CrosslinkError(
            "no biopsy decision on record — custody cannot begin for a "
            "specimen nobody decided to take")
    if c["specimen_id"]:
        raise CrosslinkError(
            "a specimen is already attached — validate BOTH sides "
            "before mutating either; a half-registered biopsy is a "
            "custody lie")
    spec = path.register_specimen(
        specimen_id, patient_id=patient_id, specimen_type="biopsy",
        site="cervix", collector=collector,
        facility=c["facility"], day=day)
    cervix.attach_specimen(patient_id, specimen_id=specimen_id)
    return {"specimen": spec["specimen_id"], "state": spec["state"],
            "histology_pending": True,
            "note": "the loop is open: specimen in custody, histology "
                    "owed; it stays visible until the result lands"}


# ---------------------------------------------------------------- 13.
# inpatient → medrec: admission STARTS the reconciliation, always.
def admissions_to_medrec(ward, medrec, *, day, by, started):
    """Every patient on the census without an open admission
    reconciliation gets one started — the transition of care is the
    moment the med list is lost, so the list is made at the moment of
    transition. Idempotent via the caller-owned `started` set: the
    second ring of the bell is not a second case."""
    started_now, already = [], []
    for row in ward.census():
        pid = row["patient"]
        if pid in started:
            already.append(pid)
            continue
        medrec.start(pid, setting="admission", day=day, by=by)
        started.add(pid)
        started_now.append(pid)
    return {"started": started_now, "already_started": sorted(already),
            "note": "the undecided drug at admission is the one that "
                    "silently stops; the one at discharge is the one "
                    "that silently disappears"}


# ---------------------------------------------------------------- 14.
# telepath → reportkit: a verified histology read leaves the system as
# a SIGNED synoptic, or it has not left.
def synoptic_signout(path, rk, *, case_id, report_id, template_id,
                     answers, signer, day):
    """Bind a verified pathology read to a signed structured synoptic.

    Both sides are validated before EITHER mutates (the seam's rule):
    the case must be reported-verified with its specimen's custody
    chain complete, and the answers must validate against the template
    — only then is the report drafted and signed, and only after the
    signature exists is the report id bound onto the specimen. A
    synoptic that fails validation mid-way must not leave a
    half-signed report pointing at a specimen."""
    case = path.reading.cases.get(case_id)
    if case is None:
        raise CrosslinkError(f"unknown pathology case: {case_id}")
    if case["state"] != "reported-verified":
        raise CrosslinkError(
            f"{case_id} is {case['state']} — a synoptic formalizes a "
            "VERIFIED read; signing a draft is signing a rumour")
    specimen_id = case.get("meta", {}).get("specimen_id")
    if specimen_id:
        spec = path._spec(specimen_id)
        if spec["state"] != "scanned":
            raise CrosslinkError(
                f"specimen {specimen_id} is at {spec['state']} — the "
                "slide is not made; there is nothing to sign out")
        if spec.get("synoptic_report"):
            raise CrosslinkError(
                f"specimen {specimen_id} already signed out as "
                f"{spec['synoptic_report']} — corrections are addenda, "
                "never silent re-signs")
    # validate FIRST (raises before anything mutates), then draft+sign
    rk.validate(template_id, answers)
    rk.draft(report_id, template_id=template_id, answers=answers,
             case_id=case_id, author_id=signer, day=day)
    signed = rk.sign(report_id, signer_id=signer, day=day)
    if specimen_id:
        path._spec(specimen_id)["synoptic_report"] = report_id
    return {"report_id": report_id, "specimen_id": specimen_id,
            "content_hash": signed["signature"]["content_hash"],
            "note": "the hash is the contract — any post-signature "
                    "edit invalidates verification"}


# ---------------------------------------------------------------- 15.
# mdt → cpoe: a board decision to treat lands as real orders — "start
# chemo regimen X" cannot stay a sentence with an owner.
def decision_to_orders(board, orders, case_id, *, prescriber_id, day,
                       prescriptions, patient_allergies=(),
                       active_codes=(), id_prefix=None,
                       override_reasons=None):
    """Turn a recorded MDT decision into drafted, screened, placed
    CPOE orders.

    prescriptions: [(code, dose_mg, doses_per_day, days), ...].

    Both sides validated before either mutates: the case must be
    DECIDED, every drug code must exist in the formulary, and allergy
    screening runs BEFORE any order is placed (allergy is fail-closed;
    a single conflict refuses the whole batch — a half-placed regimen
    is a dosing lie). Placed order ids are recorded back onto the
    case, so execution tracking counts orders, not intentions."""
    c = board._case(case_id)
    if c["state"] != "decided":
        raise CrosslinkError(
            f"{case_id} is {c['state']} — orders follow a recorded "
            "decision, not hallway consensus")
    if c.get("orders"):
        raise CrosslinkError(
            f"{case_id} already has orders {c['orders']} — a second "
            "round is a new decision, not a silent re-issue")
    if not prescriptions:
        raise CrosslinkError("a treat decision with no prescriptions "
                             "is a sentiment, not an order")
    # pre-validate every code BEFORE drafting anything
    for code, *_ in prescriptions:
        if not orders.fx.lookup(code):
            raise CrosslinkError(f"unknown drug code {code} — the "
                                 "formulary refuses before any order "
                                 "exists")
    # allergy is fail-closed and checked BEFORE any draft exists —
    # a half-drafted regimen is still a half-state
    for code, *_ in prescriptions:
        orders.fx.check_allergy(code, patient_allergies)
    prefix = id_prefix or f"MDT-{case_id}"
    # interaction screening must see what the patient is ALREADY on:
    # the caller's list plus this service's placed orders for them
    active = set(active_codes)
    for other in orders.orders.values():
        if other.patient_id == c["patient_id"] \
                and other.status == "placed":
            active.add(other.code)
    drafted = []
    for i, (code, dose_mg, doses_per_day, days) in \
            enumerate(prescriptions):
        o = orders.draft(f"{prefix}-{i}", c["patient_id"],
                         prescriber_id, code, dose_mg, doses_per_day,
                         days, indication=f"mdt-decision:{case_id}")
        orders.screen(o.oid, patient_allergies, sorted(active))
        active.add(code)  # batch members screen against each other
        drafted.append(o.oid)
    # major findings need documented overrides; check the WHOLE batch
    # before placing ANY — a half-placed regimen is a dosing lie
    needs_override = [oid for oid in drafted
                      if any(f["severity"] in ("major", "contraindicated")
                             for f in orders.orders[oid].findings)]
    if needs_override and not override_reasons:
        raise CrosslinkError(
            f"{len(needs_override)} order(s) carry major findings "
            f"({needs_override}) — the batch waits for documented "
            "override reasons; a half-placed regimen is a dosing lie")
    placed = [orders.place(
        oid,
        override_reasons=[{"finding": f["message"], "reason": r}
                          for f in orders.orders[oid].findings
                          for r in (override_reasons or [])]
        or None).oid for oid in drafted]
    c["orders"] = placed
    return {"case_id": case_id, "orders": placed,
            "note": "the board decides; the formulary screens; the "
                    "order either exists or the action is an open loop"}


# ---------------------------------------------------------------- 16.
# viewerlaunch → teleread: you cannot verify what you did not open.
#
# The worklist P0 closes here: verification of an imaging or slide
# case requires a launch session for the case's artifact BY the
# verifying reader. A report signed without pixels is a paper exercise
# — refuse it, loudly, and leave the draft visible as a draft.

def verify_with_pixels(reading, launcher, case_id, *, artifact,
                       reader_id, day):
    """Verify only after the verifying reader has opened the artifact.

    Absence is not a view: no launch session for (artifact, reader) is
    a refusal, whatever the draft says. The session id lands on the
    report so the audit trail runs pixel → prose → signature in one
    direction only."""
    rep = reading._report(case_id)
    if rep["state"] != "draft":
        raise CrosslinkError(f"report is {rep['state']}, not draft — "
                             "the pixel gate applies before signature")
    sessions = [s for s in launcher.sessions
                if s["artifact"] == artifact
                and s["reader"] == reader_id]
    if not sessions:
        raise CrosslinkError(
            f"{reader_id} never opened {artifact} — a report signed "
            "without the pixels is an opinion about a filename; open "
            "the study/slide first")
    out = reading.verify_report(case_id, reader_id=reader_id, day=day)
    out["viewer_session"] = sessions[-1]["session_id"]
    return out


# ---------------------------------------------------------------- 17.
# travelpass → surveillance: the border is the first epidemiological
# sensor. An emergency event whose text matches a notifiable condition
# (the traveler's recorded conditions and the emergency summary) is an
# importation signal and enters the surveillance ring — anonymized to
# (syndrome, area, day); the k-anonymity floor applies downstream as
# always. A traveler record with nothing notifiable says so.

def imported_case_signal(registry, surv, *, traveler_id, day,
                         base_date, area=None):
    """Feed travelpass emergency events into syndrome surveillance.

    Only NOTIFIABLE keyword matches become events; everything else is
    stated as zero-signal, never silently dropped. Returns the events
    ingested and any threshold alerts the ring produced."""
    import datetime as _dt
    sys.path.insert(0, os.path.join(os.path.dirname(__file__),
                                    "..", "travelpass"))
    import travelpass_cards as _tp  # noqa: E402
    t = registry._t(traveler_id)
    profile_text = " ".join(
        t.get("emergency_profile", {}).get("conditions") or [])
    events = [e for e in registry.emergencies
              if e["traveler_id"] == traveler_id]
    hits = []
    for e in events:
        text = (e.get("summary", "") + " " + profile_text).lower()
        for cond, syndrome in _tp.NOTIFIABLE.items():
            if cond in text:
                hits.append({
                    "syndrome": syndrome,
                    "area": area or e.get("facility", "unknown"),
                    "ts": (base_date + _dt.timedelta(
                        days=e["day"])).isoformat(),
                    "condition": cond,
                    "traveler_ref": traveler_id})
                break  # one syndrome per event — no double counting
    if not hits:
        return {"imported_signals": 0, "alerts": [],
                "note": "no notifiable condition on this traveler — "
                        "stated, not zeroed"}
    alerts = surv.ingest([{k: h[k] for k in ("syndrome", "area", "ts")}
                          for h in hits],
                         today=base_date + _dt.timedelta(days=day))
    return {"imported_signals": len(hits), "events": hits,
            "alerts": [a.to_event() if hasattr(a, "to_event")
                       else dict(a) for a in alerts],
            "notifiable_version": _tp.NOTIFIABLE_VERSION}


# ---------------------------------------------------------------- 18.
# poe_screen → travelpass: the PoE decision attaches to the traveler's
# record. Isolate-and-notify is not a suggestion — the pass record
# carries the screening outcome, and a traveler under isolation orders
# has a flag on their emergency-access card so any break-glass read
# sees it. Public-health holds ride the same key as the medicine.

def poe_entry_screen(registry, poe, traveler_id, *, list_version,
                     travel_history_21d, temp_c, symptoms, screener,
                     day):
    """Run the PoE screen for an enrolled traveler and stamp the
    outcome on their pass record. The screener's verdict is the
    border's; the platform records it with its list version so a
    stale-list refusal surfaces here, not after the flight left."""
    outcome = poe.screen(traveler_id, list_version,
                         travel_history_21d, temp_c, symptoms,
                         screener, day)
    t = registry._t(traveler_id)
    t.setdefault("poe_screens", []).append(
        {"day": day, "list_version": list_version,
         "disposition": outcome.get("disposition"),
         "screener": screener})
    if outcome.get("disposition") == "isolate-and-notify":
        t["public_health_hold"] = {
            "since_day": day, "by": screener,
            "trace_seed": outcome.get("trace_seed"),
            "note": "isolation ordered at the border — the emergency "
                    "card shows the hold until cleared"}
    return outcome


def clear_public_health_hold(registry, traveler_id, *, day, by,
                             clearance_ref):
    """A hold clears only on a documented clearance — self-declared
    recovery is not epidemiology."""
    t = registry._t(traveler_id)
    hold = t.get("public_health_hold")
    if not hold:
        raise CrosslinkError("no public-health hold on record — "
                             "absence is not a clearance either")
    if not clearance_ref:
        raise CrosslinkError("clearance needs a reference (test, "
                             "clinician note) — a verbal all-clear is "
                             "not a record")
    del t["public_health_hold"]
    t.setdefault("poe_screens", []).append(
        {"day": day, "decision": "hold-cleared", "screener": by,
         "clearance_ref": clearance_ref})
    return {"traveler_id": traveler_id, "hold_cleared_day": day,
            "clearance_ref": clearance_ref}


# ---------------------------------------------------------------- 19.
# devicelink → teleicu: a silent or quarantined device feed is a
# clinical event. A dialysis unit whose machines stopped reporting is
# not an IT ticket — it is patients being treated unmonitored. Same
# idempotent-open discipline as escalate_events: the second bell is
# not a second case.

def feed_health_to_oversight(health, icu, *, facility, day, opened):
    """Stale and quarantined feeds open oversight; live and honestly
    unmonitored feeds do not page anyone.

    `health` is a FeedHealth (or anything with .report(as_of) and a
    datetime-free report shape); `opened` is caller-owned
    (feed_id → case_id) so repeat polls are idempotent."""
    import datetime as _dt
    as_of = _dt.datetime.now()
    report = health.report(as_of) if hasattr(health, "report") \
        else health
    opened_now, already = [], []
    for feed in report.get("feeds", []):
        if feed["state"] not in ("stale", "quarantined"):
            continue
        fid = feed["session_id"]
        if fid in opened:
            already.append(opened[fid])
            continue
        severity = SEVERITY_TO_PRIORITY[
            "critical" if feed["state"] == "quarantined" else "high"]
        case_id = f"feed-{fid}"
        icu.open_oversight(case_id, patient_id=f"device:{fid}",
                           facility=facility,
                           trigger_ref=f"feed:{feed['state']}",
                           day=day, severity=severity)
        opened[fid] = case_id
        opened_now.append(case_id)
    for fid in report.get("quarantined", []):
        if fid in opened:
            continue
        case_id = f"feed-{fid}"
        icu.open_oversight(case_id, patient_id=f"device:{fid}",
                           facility=facility, trigger_ref="feed:"
                           "quarantined", day=day,
                           severity=SEVERITY_TO_PRIORITY["critical"])
        opened[fid] = case_id
        opened_now.append(case_id)
    return {"opened": opened_now, "already_open": already,
            "note": "a device that stops talking is a clinical event; "
                    "the oversight ladder is the escalation, not an "
                    "email to IT"}


# ---------------------------------------------------------------- 20.
# pacs → datasetforge: the de-id gate is the only door from clinical
# pixels to training data. Burned-in annotations refuse upstream; the
# patient key is a salted hash minted here; the de-id policy version
# rides every case. Diagnosis never reads from the dataset lane — the
# arrow points one way only.

def deid_export_to_dataset(studies, forge, study_uid, *, task_id,
                           label, labeled_by, day, salt,
                           deid_policy_version):
    """Register a PACS study as a labeled training case.

    `studies` is the pacs StudyRegistry; export_metadata(purpose=
    "research") refuses burned-in annotations before we ever touch the
    forge. The salted patient key keeps splits patient-level without
    the patient id ever landing in the dataset registry."""
    export = studies.export_metadata(study_uid, purpose="research")
    # the patient id is read from the SOURCE record, hashed, then
    # dropped — the forge never sees it
    study = studies._study(study_uid)
    patient_key = forge.patient_key_from(study["patient_id"],
                                         salt=salt)
    case = forge.register_case(
        task_id, case_ref=f"case-{study_uid}",
        source_ref=study_uid, patient_key=patient_key, label=label,
        labeled_by=labeled_by, day=day,
        deid_policy_version=deid_policy_version)
    case = dict(case)
    case["deid_export"] = {"modality": export.get("modality"),
                           "purpose": export["purpose"]}
    return case


def emergency_to_claim(registry, claims, traveler_id, *, session_id,
                       now_h, day, channel="phone"):
    """Seam 21: a recorded travelpass emergency opens a claims case.
    The claim binds to the break-glass audit session — a claim without
    the emergency audit is a story. Coverage at access rides onto the
    case: active pays, lapsed flags recovery — the invoice never gated
    the airway, and the ledger remembers. One claim per session; a
    second call returns the case that exists, not a duplicate."""
    event = next((e for e in registry.emergencies
                  if e["traveler_id"] == traveler_id
                  and e["session_id"] == session_id), None)
    if event is None:
        raise KeyError("no emergency event for this session — the claim "
                       "binds to the audit, not to a phone call")
    existing = next((c for c in claims.cases.values()
                     if c.get("session_id") == session_id), None)
    if existing is not None:
        return existing
    case = claims.intake(channel=channel, cover_ref=traveler_id,
                         claimant_name=traveler_id,
                         summary=event["summary"], now_h=now_h, day=day)
    case["session_id"] = session_id
    case["coverage_at_access"] = ("active" if event["payable"]
                                  else "lapsed")
    case["recovery_flagged"] = not event["payable"]
    claims.audit.append({"event": "claim_from_emergency",
                         "case": case["case_id"],
                         "session_id": session_id, "day": day})
    return case


def premium_to_fund(vhc, fund, ref, *, day):
    """Seam 22: a VHC premium allocates into the fund's buckets the
    moment it is collected — a premium that lands unsplit is
    administration money by default, which is how schemes eat claims
    money. Idempotent per ref: re-running the seam re-allocates
    nothing."""
    pays = [p for p in vhc.payments if p["ref"] == ref
            and p["kind"] in ("premium", "extension")]
    if not pays:
        raise KeyError("no premium for this ref — the fund is fed by "
                       "the ledger, not by declarations")
    already = {e["ref"] for e in fund.entries
               if e["kind"] == "allocation"}
    posted = []
    for p in pays:
        alloc_key = "%s:%s" % (p["ref"], p["day"])
        if alloc_key in already or p["minor"] == 0:
            continue  # exempt issues allocate nothing, honestly
        posted.append(fund.post_premium(alloc_key,
                                        amount_minor=p["minor"],
                                        day=day))
    return {"allocated": len(posted), "ref": ref}
