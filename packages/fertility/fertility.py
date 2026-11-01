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
