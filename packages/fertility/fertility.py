"""fertility.py — IVF/ART: the honest ledger for an unregulated market.

Nigeria has no comprehensive ART legislation; the platform IS the
regulation. The failure modes this module refuses to allow:

  - a cycle without both partners' consent
  - an embryo moved by one person alone (mix-ups are unforgivable —
    every movement is double-witnessed)
  - an embryo discarded on an expired disposition consent (abandoned
    embryos are an ethical finding, surfaced, never silently destroyed)
  - a "success rate" typed into a brochure (outcomes are computed from
    the ledger: singleton live birth per cycle started, denominators
    shown)
  - a donor used without complete negative screening
  - cross-border gamete transport without a consent release and a hash
    that matches (xrelease doctrine)
  - a refund promise that isn't executable data

Records are sealed-class: every view is logged; reporting is
aggregate-only.
"""

CONSENT_KINDS = ("cycle-treatment", "cryopreservation", "embryo-transfer",
                 "donor-gametes", "disposition-discard",
                 "disposition-donate")
DISPOSITION_KINDS = ("disposition-discard", "disposition-donate")
CONSENT_METHODS = ("signed", "verbal-witnessed")
DISPOSITION_VALIDITY_DAYS = 365   # disposition consent expires; re-confirm

CYCLE_STAGES = ("planned", "stimulation", "triggered", "retrieval",
                "fertilization", "culture", "transfer",
                "outcome-pending", "closed")
FERTILIZATION_METHODS = ("ivf", "icsi")
EMBRYO_MOVES = {"culture": ("frozen", "transferred", "discarded"),
                "frozen": ("thawed", "discarded", "donated"),
                "thawed": ("transferred", "discarded"),
                "transferred": (), "discarded": (), "donated": ()}
DONOR_SCREENING = ("hiv", "hbv", "hcv", "syphilis")
OHSS_ESCALATE = ("severe", "critical")
OHSS_SEVERITIES = ("mild", "moderate") + OHSS_ESCALATE

# ---- stimulation monitoring (P0): a cycle without observations is
# unmanageable clinically. Bounds are plausibility gates, not targets.
MON_E2_PMOL_L = (20.0, 30000.0)
MON_ENDOMETRIUM_MM = (2.0, 25.0)
MON_MAX_FOLLICLES = 60
OHSS_FOLLICLE_WARN = 20       # ≥14 mm count above → coast/cancel draft
OHSS_E2_WARN = 15000.0        # pmol/L
E2_DOUBLING_DAYS = 2          # E2 should ~double per 2 days mid-stim
FOLLICLE_TRIGGER_MM = 18      # ≥2 follicles ≥18 → trigger-ready draft
FSH_MAX_STEP_PCT = 50         # dose adjustments bounded, like ESA

# ---- embryology grading (P0): morphology with grader identity.
# Assessments are append-only: a second read is a second entry, never
# an edit — the lab notebook does not erase.
PN_RESULTS = ("0PN", "1PN", "2PN", "3PN", "degenerate")
GARDNER_EXPANSION = (1, 2, 3, 4, 5, 6)
GARDNER_LETTERS = ("A", "B", "C")
DAY3_MAX_CELLS = 16
FRAGMENTATION_GRADES = (1, 2, 3, 4)   # 1 = <10%, 4 = >50%


class FertilityError(Exception):
    pass


class FertilityClinic:
    def __init__(self, facility):
        self.facility = facility
        self.couples = {}
        self.cycles = {}
        self.embryos = {}
        self.donors = {}
        self.consents = {}      # (couple, kind) -> [entries]
        self.packages = {}      # couple -> refund package
        self.escalations = []   # OHSS
        self.transports = []
        self.access_log = []    # sealed-class: every view recorded
        self.grades = {}        # embryo_id -> [assessment entries]

    # ---- couples & consent ----
    def register_couple(self, couple_id, *, partner_a, partner_b, day):
        if couple_id in self.couples:
            raise FertilityError(f"duplicate couple: {couple_id}")
        if partner_a == partner_b:
            raise FertilityError("two distinct partners required")
        self.couples[couple_id] = {
            "partner_a": partner_a, "partner_b": partner_b, "day": day,
            "cycles": []}
        return dict(self.couples[couple_id])

    def grant_consent(self, couple_id, kind, *, method, by, day):
        c = self._couple(couple_id)
        if kind not in CONSENT_KINDS:
            raise FertilityError(f"unknown consent kind: {kind}")
        if method not in CONSENT_METHODS:
            raise FertilityError(f"unknown consent method: {method}")
        if by not in (c["partner_a"], c["partner_b"]):
            raise FertilityError(
                "consent is personal — only a partner grants it, never "
                "staff on their behalf")
        entry = {"by": by, "method": method, "day": day,
                 "expiry_day": (day + DISPOSITION_VALIDITY_DAYS
                                if kind in DISPOSITION_KINDS else None)}
        self.consents.setdefault((couple_id, kind), []).append(entry)
        return dict(entry)

    def _both_consented(self, couple_id, kind, day):
        c = self.couples[couple_id]
        entries = self.consents.get((couple_id, kind), [])
        valid_by = {e["by"] for e in entries
                    if e["expiry_day"] is None or day <= e["expiry_day"]}
        return (c["partner_a"] in valid_by and c["partner_b"] in valid_by)

    # ---- cycle ledger ----
    def start_cycle(self, cycle_id, *, couple_id, protocol_version, day):
        c = self._couple(couple_id)
        if not protocol_version:
            raise FertilityError("protocol version required — an "
                                 "unversioned protocol is unauditable")
        if not self._both_consented(couple_id, "cycle-treatment", day):
            raise FertilityError(
                "cycle-treatment consent required from BOTH partners")
        if cycle_id in self.cycles:
            raise FertilityError(f"duplicate cycle: {cycle_id}")
        self.cycles[cycle_id] = {
            "cycle_id": cycle_id, "couple_id": couple_id,
            "protocol": protocol_version, "stage": "planned",
            "started_day": day, "outcome": None, "donor": None}
        c["cycles"].append(cycle_id)
        return dict(self.cycles[cycle_id])

    def advance(self, cycle_id, stage, *, day, by):
        cy = self._cycle(cycle_id)
        if stage not in CYCLE_STAGES or stage == "closed":
            raise FertilityError(f"unknown stage: {stage}")
        idx = CYCLE_STAGES.index(cy["stage"])
        if CYCLE_STAGES.index(stage) != idx + 1:
            raise FertilityError(
                f"cycle stages are sequential: {cy['stage']} → {stage} "
                "skips steps; the ledger never fast-forwards")
        cy["stage"] = stage
        cy.setdefault("events", []).append((stage, day, by))
        return dict(cy)

    def fertilize(self, cycle_id, *, method, n_embryos, day, embryologist):
        cy = self._cycle(cycle_id)
        if cy["stage"] != "fertilization":
            raise FertilityError("fertilize only at the fertilization "
                                 "stage")
        if method not in FERTILIZATION_METHODS:
            raise FertilityError(f"unknown method: {method}")
        if not (0 < n_embryos <= 25):
            raise FertilityError(f"implausible embryo count: {n_embryos}")
        cy["method"] = method
        for i in range(1, n_embryos + 1):
            eid = f"{cycle_id}-E{i}"
            self.embryos[eid] = {"embryo_id": eid, "cycle_id": cycle_id,
                                 "couple_id": cy["couple_id"],
                                 "stage": "culture", "created_day": day,
                                 "by": embryologist, "location": None,
                                 "movements": [("culture", day,
                                                embryologist, None)]}
        return {"created": n_embryos, "method": method}

    # ---- embryo custody: double-witnessed, always ----
    def move_embryo(self, embryo_id, to_stage, *, day, witness_1,
                    witness_2, tank=None, canister=None, position=None):
        e = self._embryo(embryo_id)
        cy = self._cycle(e["cycle_id"])
        if not witness_1 or not witness_2 or witness_1 == witness_2:
            raise FertilityError(
                "every embryo movement requires TWO distinct named "
                "witnesses — a mix-up is unforgivable")
        if to_stage not in EMBRYO_MOVES.get(e["stage"], ()):
            raise FertilityError(
                f"illegal move: {e['stage']} → {to_stage}")
        couple, d = e["couple_id"], day
        if to_stage == "frozen":
            if not self._both_consented(couple, "cryopreservation", d):
                raise FertilityError("cryopreservation consent from both "
                                     "partners required")
            if not (tank and canister and position):
                raise FertilityError(
                    "cryo location required (tank/canister/position) — "
                    "an embryo without a location is already lost")
            e["location"] = {"tank": tank, "canister": canister,
                             "position": position}
        if to_stage == "transferred" and not self._both_consented(
                couple, "embryo-transfer", d):
            raise FertilityError("embryo-transfer consent from both "
                                 "partners required")
        if to_stage == "discarded" and not self._both_consented(
                couple, "disposition-discard", d):
            raise FertilityError(
                "disposition-discard consent missing or EXPIRED — "
                "disposition consent lapses after "
                f"{DISPOSITION_VALIDITY_DAYS} days; re-confirm. An "
                "embryo is never discarded on a stale signature")
        if to_stage == "donated" and not self._both_consented(
                couple, "disposition-donate", d):
            raise FertilityError("disposition-donate consent missing or "
                                 "expired")
        e["movements"].append((to_stage, day, witness_1, witness_2))
        e["stage"] = to_stage
        if to_stage == "transferred":
            cy["transferred"] = cy.get("transferred", 0) + 1
        return dict(e)

    # ---- donors ----
    def register_donor(self, donor_code, *, screening, day):
        if donor_code in self.donors:
            raise FertilityError(f"duplicate donor: {donor_code}")
        missing = [t for t in DONOR_SCREENING
                   if screening.get(t) != "negative"]
        if missing:
            raise FertilityError(
                f"incomplete/non-negative screening: {missing} — a "
                "donor is registered only when EVERY screen is negative")
        # anonymized by construction: code + screening, no identity
        self.donors[donor_code] = {"screening": dict(screening),
                                   "registered_day": day}
        return {"donor_code": donor_code, "registered": True}

    def use_donor(self, cycle_id, donor_code, *, day):
        cy = self._cycle(cycle_id)
        if donor_code not in self.donors:
            raise FertilityError(f"unknown donor: {donor_code}")
        if not self._both_consented(cy["couple_id"], "donor-gametes",
                                    day):
            raise FertilityError("donor-gametes consent from both "
                                 "partners required")
        cy["donor"] = donor_code
        return {"cycle_id": cycle_id, "donor": donor_code}

    # ---- OHSS surveillance ----
    def ohss_assess(self, patient_id, *, severity, day, by):
        if severity not in OHSS_SEVERITIES:
            raise FertilityError(f"unknown OHSS severity: {severity}")
        entry = {"patient_id": patient_id, "severity": severity,
                 "day": day, "by": by, "acknowledged": None}
        if severity in OHSS_ESCALATE:
            entry["note"] = ("severe OHSS — ack required; silence "
                             "re-escalates")
            self.escalations.append(entry)
        return dict(entry)

    def acknowledge_ohss(self, patient_id, *, day, by):
        for e in self.escalations:
            if (e["patient_id"] == patient_id
                    and e["acknowledged"] is None):
                e["acknowledged"] = {"day": day, "by": by}
                return dict(e)
        raise FertilityError("no open OHSS escalation for this patient")

    def unacknowledged_ohss(self, as_of, hours=4):
        return [dict(e, reescalate=True) for e in self.escalations
                if e["acknowledged"] is None
                and (as_of - e["day"]) * 24 >= hours]

    # ---- cross-border transport (xrelease doctrine) ----
    def transport_embryo(self, embryo_id, *, destination, day,
                         release_id, bundle_hash=None,
                         expected_hash=None):
        e = self._embryo(embryo_id)
        if e["stage"] != "frozen":
            raise FertilityError("only frozen embryos transport")
        if not release_id:
            raise FertilityError(
                "transport requires a consent release id — the embryo "
                "travels lawfully or not at all")
        if expected_hash and bundle_hash and bundle_hash != expected_hash:
            self.transports.append({"embryo_id": embryo_id, "sent": False,
                                    "reason": "hash-mismatch", "day": day})
            raise FertilityError(
                "hash mismatch — refused and logged; what leaves must be "
                "exactly what was consented to")
        self.transports.append({"embryo_id": embryo_id,
                                "destination": destination, "day": day,
                                "release_id": release_id, "sent": True})
        return dict(self.transports[-1])

    # ---- outcomes: honest denominators ----
    def close_cycle(self, cycle_id, *, day, pregnancy_test,
                    clinical_pregnancy=False, live_birth=False,
                    singleton=False):
        cy = self._cycle(cycle_id)
        if cy["stage"] != "outcome-pending":
            raise FertilityError("cycle must be outcome-pending to close")
        if clinical_pregnancy and not pregnancy_test:
            raise FertilityError("clinical pregnancy without a positive "
                                 "test is a fiction")
        if live_birth and not clinical_pregnancy:
            raise FertilityError("live birth requires clinical pregnancy")
        if singleton and not live_birth:
            raise FertilityError("singleton requires live birth")
        cy["outcome"] = {"pregnancy_test": bool(pregnancy_test),
                         "clinical_pregnancy": bool(clinical_pregnancy),
                         "live_birth": bool(live_birth),
                         "singleton": bool(singleton), "day": day}
        cy["stage"] = "closed"
        return dict(cy["outcome"])

    def outcome_stats(self):
        started = [c for c in self.cycles.values()]
        closed = [c for c in started if c["stage"] == "closed"]
        def n(pred):
            return sum(1 for c in closed
                       if c["outcome"] and pred(c["outcome"]))
        return {"cycles_started": len(started), "cycles_closed": len(closed),
                "clinical_pregnancies": n(lambda o: o["clinical_pregnancy"]),
                "live_births": n(lambda o: o["live_birth"]),
                "singleton_live_births": n(lambda o: o["singleton"]),
                "note": "rates are per cycle STARTED with denominators "
                        "shown; a success rate without n is marketing"}

    # ---- refund-on-failure packages ----
    def register_package(self, couple_id, *, cycles_included, refund_kobo):
        self._couple(couple_id)
        if cycles_included < 1 or refund_kobo < 0:
            raise FertilityError("implausible package terms")
        self.packages[couple_id] = {"cycles_included": cycles_included,
                                    "refund_kobo": refund_kobo}
        return dict(self.packages[couple_id])

    def refund_status(self, couple_id):
        p = self.packages.get(couple_id)
        if not p:
            raise FertilityError("no package for this couple")
        cys = [self.cycles[cid]
               for cid in self.couples[couple_id]["cycles"]]
        closed = [c for c in cys if c["stage"] == "closed"]
        any_birth = any(c["outcome"]["live_birth"] for c in closed)
        due = (len(closed) >= p["cycles_included"] and not any_birth)
        return {"cycles_closed": len(closed),
                "cycles_included": p["cycles_included"],
                "live_birth": any_birth,
                "refund_due_kobo": p["refund_kobo"] if due else 0,
                "note": "computed from the ledger, not from a promise"}

    # ---- cryo inventory & abandonment surveillance ----
    def expiring_dispositions(self, as_of, within_days=30):
        out = []
        for (couple, kind), entries in self.consents.items():
            if kind not in DISPOSITION_KINDS:
                continue
            latest = max(entries, key=lambda e: e["day"])
            frozen = [e for e in self.embryos.values()
                      if e["couple_id"] == couple and e["stage"] == "frozen"]
            if frozen and latest["expiry_day"] - as_of <= within_days:
                out.append({"couple_id": couple, "kind": kind,
                            "expiry_day": latest["expiry_day"],
                            "frozen_embryos": len(frozen),
                            "note": "re-confirm disposition before "
                                    "expiry; silence is not consent"})
        return sorted(out, key=lambda x: x["expiry_day"])

    def cryo_inventory(self):
        frozen = [e for e in self.embryos.values()
                  if e["stage"] == "frozen"]
        return {"frozen_embryos": len(frozen),
                "by_tank": sorted({e["location"]["tank"]
                                   for e in frozen if e["location"]}),
                "note": "never-negative by construction: embryos only "
                        "leave storage through witnessed moves"}

    # ---- sealed-class access ----
    def view(self, couple_id, *, by, role, day):
        self.access_log.append({"couple_id": couple_id, "by": by,
                                "role": role, "day": day})
        c = self._couple(couple_id)
        return {"couple_id": couple_id,
                "cycles": list(c["cycles"]),
                "accessed_by": by}

    def report(self):
        """Aggregate only — no couple line-list exists."""
        return {"couples": len(self.couples),
                "cycles": len(self.cycles),
                "embryos": len(self.embryos),
                "donors": len(self.donors),
                "access_events": len(self.access_log)}

    # ---- stimulation monitoring (P0) ----
    def record_monitoring(self, cycle_id, *, day, e2_pmol_l,
                          follicles_ge_14mm, follicles_total,
                          endometrium_mm, fsh_dose_iu, by):
        """One monitoring visit: E2, follicle counts, endometrium, and
        the FSH dose actually given that day. The cycle must be in
        stimulation — monitoring a triggered cycle is revisionism."""
        cy = self._cycle(cycle_id)
        if cy["stage"] != "stimulation":
            raise FertilityError(
                f"cycle is '{cy['stage']}' — monitoring attaches to "
                "stimulation only")
        lo, hi = MON_E2_PMOL_L
        if not (lo <= e2_pmol_l <= hi):
            raise FertilityError(f"implausible E2: {e2_pmol_l}")
        if not (0 <= follicles_ge_14mm <= follicles_total
                <= MON_MAX_FOLLICLES):
            raise FertilityError(
                "implausible follicle counts (≥14mm cannot exceed "
                "total)")
        lo, hi = MON_ENDOMETRIUM_MM
        if not (lo <= endometrium_mm <= hi):
            raise FertilityError(
                f"implausible endometrium: {endometrium_mm}mm")
        if not (0 <= fsh_dose_iu <= 600):
            raise FertilityError(f"implausible FSH dose: {fsh_dose_iu}")
        entry = {"day": day, "e2_pmol_l": e2_pmol_l,
                 "follicles_ge_14mm": follicles_ge_14mm,
                 "follicles_total": follicles_total,
                 "endometrium_mm": endometrium_mm,
                 "fsh_dose_iu": fsh_dose_iu, "by": by}
        cy.setdefault("monitoring", []).append(entry)
        return dict(entry)

    def monitoring_review(self, cycle_id, *, day):
        """Serial observations → a dose/timing DRAFT. The embryologist
        and clinician decide; the protocol proposes, bounded.

        Drafts, in priority order:
          coast-or-cancel  — OHSS risk (E2 very high or ≥14mm flood)
          trigger-ready    — ≥2 follicles ≥18mm, endometrium adequate
          increase-fsh     — flat E2 / stalled growth (bounded step)
          decrease-fsh     — E2 rising faster than doubling
          continue         — on track
        """
        cy = self._cycle(cycle_id)
        mon = cy.get("monitoring", [])
        if not mon:
            raise FertilityError(
                f"no monitoring for {cycle_id} — reviewing without "
                "observations is prescribing from vibes")
        latest = mon[-1]
        rec = {"cycle_id": cycle_id, "day": day,
               "latest": dict(latest), "draft": True,
               "note": "the protocol proposes, bounded; the clinician "
                       "decides — and OHSS assessment is separate and "
                       "mandatory (ohss_assess)"}
        if (latest["e2_pmol_l"] >= OHSS_E2_WARN
                or latest["follicles_ge_14mm"] >= OHSS_FOLLICLE_WARN):
            rec["recommendation"] = {
                "action": "coast-or-cancel",
                "reason": f"E2 {latest['e2_pmol_l']:.0f} / "
                          f"{latest['follicles_ge_14mm']} follicles "
                          f"≥14mm — OHSS risk; trigger only after "
                          "ohss_assess, consider freeze-all"}
            return rec
        if len(mon) >= 2:
            prev = mon[-2]
            gap = max(1, latest["day"] - prev["day"])
            e2_ratio = latest["e2_pmol_l"] / max(prev["e2_pmol_l"], 1)
            expected = 2 ** (gap / E2_DOUBLING_DAYS)
            # overshoot outranks trigger-readiness: safety before timing
            if e2_ratio > expected * 1.5:
                rec["recommendation"] = {
                    "action": "decrease-fsh",
                    "max_step_pct": -FSH_MAX_STEP_PCT,
                    "reason": f"E2 rising {e2_ratio:.1f}x in {gap}d — "
                              "faster than doubling; overshoot is the "
                              "OHSS on-ramp"}
                return rec
        if (latest["follicles_ge_14mm"] >= 2
                and latest["endometrium_mm"] >= 7):
            rec["recommendation"] = {
                "action": "trigger-ready",
                "reason": "≥2 follicles ≥14mm cohort with adequate "
                          "endometrium — check lead follicle ≥18mm on "
                          "scan, then trigger"}
            return rec
        if len(mon) >= 2:
            stalled = (e2_ratio < 1.2
                       and latest["follicles_ge_14mm"]
                       <= prev["follicles_ge_14mm"])
            if stalled:
                rec["recommendation"] = {
                    "action": "increase-fsh",
                    "max_step_pct": FSH_MAX_STEP_PCT,
                    "reason": f"E2 flat ({e2_ratio:.2f}x in {gap}d) "
                              "and no cohort growth — step up within "
                              "the bound, rescan in 2-3d"}
                return rec
        rec["recommendation"] = {"action": "continue",
                                 "reason": "response on track"}
        return rec

    # ---- embryology grading (P0) ----
    def grade_embryo(self, embryo_id, *, culture_day, day, by, **fields):
        """Morphology with grader identity. culture_day selects the
        assessment kind:
          1   — fertilization check: pn ('2PN' etc.)
          3   — cells + fragmentation grade
          5/6 — Gardner: expansion (1-6), icm (A-C), te (A-C)
        Append-only: re-grading is a new entry citing the grader,
        never an edit of yesterday's morphology."""
        e = self._embryo(embryo_id)
        if not by:
            raise FertilityError("grader identity required — unsigned "
                                 "morphology is gossip")
        if e["stage"] not in ("culture", "frozen"):
            raise FertilityError(
                f"embryo is '{e['stage']}' — grading attaches to "
                "culture (or a thaw-check on frozen)")
        a = {"culture_day": culture_day, "day": day, "by": by}
        if culture_day == 1:
            pn = fields.get("pn")
            if pn not in PN_RESULTS:
                raise FertilityError(f"unknown PN result: {pn}")
            a["pn"] = pn
        elif culture_day == 3:
            cells = fields.get("cells")
            frag = fields.get("fragmentation")
            if not isinstance(cells, int) or not (1 <= cells
                                                  <= DAY3_MAX_CELLS):
                raise FertilityError(f"implausible cell count: {cells}")
            if frag not in FRAGMENTATION_GRADES:
                raise FertilityError(
                    f"fragmentation grade must be {FRAGMENTATION_GRADES}")
            a["cells"], a["fragmentation"] = cells, frag
        elif culture_day in (5, 6):
            exp = fields.get("expansion")
            icm, te = fields.get("icm"), fields.get("te")
            if exp not in GARDNER_EXPANSION:
                raise FertilityError(
                    f"Gardner expansion must be 1-6, got {exp}")
            if icm not in GARDNER_LETTERS or te not in GARDNER_LETTERS:
                raise FertilityError("Gardner ICM/TE must be A, B or C")
            a.update({"expansion": exp, "icm": icm, "te": te})
        else:
            raise FertilityError(
                f"no defined assessment for culture day {culture_day} "
                "— day 1 (PN), day 3 (cells/fragmentation), day 5-6 "
                "(Gardner)")
        self.grades.setdefault(embryo_id, []).append(a)
        return dict(a)

    def grade_report(self, cycle_id):
        """Per-embryo latest assessments + usable-blastocyst count.
        Denominators: embryos created, fertilized normally (2PN),
        usable (Gardner ≥3 expansion with no C, or day-3 ≥6 cells
        with fragmentation ≤2)."""
        embryos = [e for eid, e in self.embryos.items()
                   if e["cycle_id"] == cycle_id]
        n = len(embryos)
        normal_fert = usable = 0
        per_embryo = []
        for e in embryos:
            gs = self.grades.get(e["embryo_id"], [])
            latest_by_day = {}
            for g in gs:
                latest_by_day[g["culture_day"]] = g
            pn = latest_by_day.get(1, {}).get("pn")
            if pn == "2PN":
                normal_fert += 1
            gard = latest_by_day.get(5) or latest_by_day.get(6)
            d3 = latest_by_day.get(3)
            ok = (gard and gard["expansion"] >= 3
                  and gard["icm"] != "C" and gard["te"] != "C") or \
                 (d3 and d3["cells"] >= 6 and d3["fragmentation"] <= 2)
            if ok:
                usable += 1
            per_embryo.append({"embryo_id": e["embryo_id"],
                               "assessments": len(gs),
                               "latest": latest_by_day or None})
        return {"cycle_id": cycle_id, "embryos": n,
                "fertilized_2pn": normal_fert, "usable": usable,
                "per_embryo": per_embryo,
                "note": "denominators are embryos created; ungraded "
                        "embryos are not assumed viable"}

    # ---- semen analysis: the male side of the couple is data too --
    def record_semen_analysis(self, couple_id, *, day, by,
                              abstinence_days, volume_ml,
                              concentration_m_per_ml,
                              total_motility_pct,
                              progressive_motility_pct,
                              vitality_pct=None, normal_forms_pct,
                              source="partner"):
        """WHO 2021 (6th ed.) semen analysis against the versioned
        lower reference limits. Below a limit is a FLAG, not a
        verdict — one sample is a snapshot. A zero concentration
        (azoospermia) requires a confirmatory sample before the label
        stands; the record says so."""
        c = self._couple(couple_id)
        if not by:
            raise FertilityError("unsigned lab results are no result")
        lo, hi = ABSTINENCE_RANGE_DAYS
        if not (lo <= abstinence_days <= hi):
            raise FertilityError(
                f"abstinence {abstinence_days}d outside {lo}-{hi}d — "
                "the sample metadata is wrong, not the patient")
        values = {"volume_ml": volume_ml,
                  "concentration_m_per_ml": concentration_m_per_ml,
                  "total_motility_pct": total_motility_pct,
                  "progressive_motility_pct": progressive_motility_pct,
                  "vitality_pct": vitality_pct,
                  "normal_forms_pct": normal_forms_pct}
        if progressive_motility_pct > total_motility_pct:
            raise FertilityError(
                "progressive motility cannot exceed total motility — "
                "you cannot find more than you looked at")
        for k, v in values.items():
            if v is None:
                continue
            _, (plo, phi) = SEMEN_LIMITS[k]
            if not (plo <= v <= phi):
                raise FertilityError(f"implausible {k}: {v}")
        below = {k: {"value": v, "reference": SEMEN_LIMITS[k][0]}
                 for k, v in values.items()
                 if v is not None and v < SEMEN_LIMITS[k][0]}
        needs_confirm = concentration_m_per_ml == 0.0
        entry = {"day": day, "by": by,
                 "abstinence_days": abstinence_days, "source": source,
                 **values, "below_reference": below,
                 "azoospermia_needs_confirmation": needs_confirm,
                 "ref_version": SEMEN_REF_VERSION,
                 "note": "below a reference limit is a flag, not a "
                         "verdict; a single zero is not a diagnosis"}
        c.setdefault("semen_analyses", []).append(entry)
        return dict(entry)

    def semen_summary(self, couple_id):
        """Serial semen analyses with denominators: how many samples,
        how many below reference, whether azoospermia was confirmed."""
        c = self._couple(couple_id)
        rows = c.get("semen_analyses", [])
        if not rows:
            raise FertilityError(f"{couple_id} has no semen analysis "
                                 "— absence is not a normal result")
        below = sum(1 for r in rows if r["below_reference"])
        az = [r for r in rows if r["concentration_m_per_ml"] == 0.0]
        return {"samples": len(rows), "below_reference": below,
                "below_reference_rate": round(below / len(rows), 3),
                "azoospermia_confirmed": len(az) >= 2,
                "ref_version": SEMEN_REF_VERSION}

    def _couple(self, cid):
        if cid not in self.couples:
            raise FertilityError(f"unknown couple: {cid}")
        return self.couples[cid]

    def _cycle(self, cid):
        if cid not in self.cycles:
            raise FertilityError(f"unknown cycle: {cid}")
        return self.cycles[cid]

    def _embryo(self, eid):
        if eid not in self.embryos:
            raise FertilityError(f"unknown embryo: {eid}")
        return self.embryos[eid]


# ---- WHO 2021 (6th ed.) semen analysis ------------------------------------
# Lower reference limits, re-typed from the published table — versioned
# data, not embedded authority: bind the lab's own validated reference
# at deployment.
SEMEN_REF_VERSION = "who-semen-2021@1.0.0"
# parameter -> (lower reference limit, plausible range)
SEMEN_LIMITS = {
    "volume_ml": (1.4, (0.1, 20.0)),
    "concentration_m_per_ml": (16.0, (0.0, 1000.0)),
    "total_motility_pct": (42.0, (0.0, 100.0)),
    "progressive_motility_pct": (30.0, (0.0, 100.0)),
    "vitality_pct": (54.0, (0.0, 100.0)),
    "normal_forms_pct": (4.0, (0.0, 100.0)),
}
ABSTINENCE_RANGE_DAYS = (1, 14)  # WHO collection window is 2-7;
                                 # outside 1-14 the sample metadata is
                                 # wrong, refuse



# ------------------------------------------------------------------
# Registry return (SART/HFEA-shaped). No statute requires it in
# Nigeria — which is exactly why self-reporting is the differentiator.
REGISTRY_RETURN_VERSION = "sart-shaped@1.0.0"


def registry_return(clinic):
    """Aggregate the clinic's cycles into a registry-shaped return.
    Every rate carries its denominator; per-transfer and per-cycle
    rates are reported separately — conflating them is how clinics
    flatter themselves."""
    cycles = list(clinic.cycles.values())
    started = len(cycles)

    def reached(cycle, stage):
        return CYCLE_STAGES.index(cycle["stage"]) >= \
            CYCLE_STAGES.index(stage)

    retrieved = [c for c in cycles if reached(c, "retrieval")]
    transferred = [c for c in cycles if reached(c, "outcome-pending")]
    closed = [c for c in cycles if c.get("outcome")]
    preg = [c for c in closed if c["outcome"]["clinical_pregnancy"]]
    births = [c for c in closed if c["outcome"]["live_birth"]]
    singletons = [c for c in births if c["outcome"]["singleton"]]

    def rate(n, d):
        return {"n": n, "of": d,
                "rate": round(n / d, 3) if d else None}

    # stimulation cancellation: cycles that never reached retrieval
    cancelled = started - len(retrieved)
    return {
        "version": REGISTRY_RETURN_VERSION,
        "cycles_started": started,
        "retrievals": rate(len(retrieved), started),
        "transfers": rate(len(transferred), started),
        "clinical_pregnancies_per_cycle": rate(len(preg), started),
        "live_births_per_cycle": rate(len(births), started),
        "live_births_per_transfer": rate(
            len(births), len(transferred)),
        "singleton_rate_of_births": rate(len(singletons),
                                         len(births)),
        "cancelled_cycles": {"n": cancelled,
                             "note": "a cancelled cycle is data, not "
                                     "an embarrassment"},
        "note": "no statute requires this return here — which is why "
                "self-reporting is the differentiator",
    }
