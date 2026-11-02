"""sepsis.py — sepsis bundle clocks with per-element completion.

Sepsis is the condition where HOURS are the unit of mortality: every
hour of delayed antibiotics raises it. The bundle (Surviving Sepsis
Campaign-shaped) is a set of timed elements from time-zero — and the
unit of failure is the ELEMENT, not the patient: 'bundle not met'
without saying WHICH element breached is a number nobody can act on.

Doctrine:
  - time-zero is a documented event with a source (clinical gestalt,
    NEWS2 escalation, qSOFA) — a clock without a documented start is
    a clock nobody can audit
  - every element has a deadline; uncompleted AND unwaived past the
    deadline is a BREACH, reported overdue-first
  - conditional elements are waived EXPLICITLY with a reason ('not
    hypotensive'), never silently skipped — an un-addressed element
    is a breach, not an exemption
  - the core three (lactate, cultures, antibiotics) can never be
    waived — suspicion of sepsis IS the indication
  - cultures BEFORE antibiotics: a violation is recorded and flagged,
    not erased — the lab notebook does not erase
  - the report shows per-element met-rates with denominators and the
    all-or-nothing bundle compliance: a rate without n is marketing

Time model: integer HOURS on the same epoch grid the rest of the
system uses for days (day * 24 + hour). Sepsis does not wait for
midnight, so neither does the clock.
"""

SEPSIS_BUNDLE_VERSION = "sepsis-bundle@1.0.0"   # SSC 2021-shaped

# element -> hours from time-zero by which it is due
BUNDLE_ELEMENTS = {
    # hour-1 core: suspicion IS the indication — never waivable
    "lactate": 1,
    "blood-cultures": 1,
    "broad-spectrum-antibiotics": 1,
    # conditional: required only when physiology demands
    "fluids-30mlkg": 3,       # hypotension or lactate >= 4
    "vasopressors": 3,        # MAP < 65 during/after fluids
    "repeat-lactate": 6,      # initial lactate elevated (> 2)
}
CORE_ELEMENTS = ("lactate", "blood-cultures",
                 "broad-spectrum-antibiotics")
# sequencing rules: element -> elements that must be completed first
# (a violation is recorded and flagged — never erased, never hidden)
REQUIRES_FIRST = {"broad-spectrum-antibiotics": ("blood-cultures",)}
TIME_ZERO_SOURCES = ("clinical-gestalt", "news2-escalation", "qsofa")


class SepsisError(Exception):
    pass


class SepsisClock:
    def __init__(self):
        self.clocks = {}   # patient_id -> clock

    def start_clock(self, patient_id, *, zero_hour, by, source):
        if patient_id in self.clocks:
            raise SepsisError(
                f"clock already running for {patient_id} — a second "
                "suspicion is not a second clock; the first one is "
                "the one that matters")
        if source not in TIME_ZERO_SOURCES:
            raise SepsisError(
                f"unknown time-zero source: {source} — a clock without "
                "a documented start is a clock nobody can audit")
        if not by:
            raise SepsisError("unsigned time-zero is no time-zero")
        self.clocks[patient_id] = {
            "patient_id": patient_id, "zero_hour": zero_hour,
            "source": source, "started_by": by,
            "elements": {},        # element -> completion record
            "waivers": {},         # element -> waiver record
            "bundle_version": SEPSIS_BUNDLE_VERSION}
        return dict(self.clocks[patient_id])

    def complete(self, patient_id, element, *, hour, by, note=None):
        """Record an element done. Sequencing rules are enforced as
        FLAGS, not refusals: if antibiotics went in before cultures,
        that happened — the ledger records it and names it."""
        if element not in BUNDLE_ELEMENTS:
            raise SepsisError(f"unknown bundle element: {element}")
        c = self._c(patient_id)
        if element in c["elements"]:
            raise SepsisError(
                f"{element} already recorded — a repeat dose is a new "
                "order, not an edit")
        if element in c["waivers"]:
            raise SepsisError(f"{element} was waived — completing it "
                              "now contradicts the waiver; one or the "
                              "other, the ledger keeps both honest")
        if not by:
            raise SepsisError("unsigned completion is no completion")
        if hour < c["zero_hour"]:
            raise SepsisError("element completed before time-zero — "
                              "check the clock, don't bend it")
        violation = [req for req in REQUIRES_FIRST.get(element, ())
                     if req not in c["elements"]]
        c["elements"][element] = {
            "hour": hour, "by": by, "note": note,
            "elapsed_hours": hour - c["zero_hour"],
            "due_by": c["zero_hour"] + BUNDLE_ELEMENTS[element],
            "met": hour <= c["zero_hour"] + BUNDLE_ELEMENTS[element],
            "sequence_violation": violation or None}
        return dict(c["elements"][element])

    def waive(self, patient_id, element, *, hour, by, reason):
        """Conditional elements are waived EXPLICITLY. The core three
        refuse: suspicion of sepsis IS the indication for them."""
        if element not in BUNDLE_ELEMENTS:
            raise SepsisError(f"unknown bundle element: {element}")
        if element in CORE_ELEMENTS:
            raise SepsisError(
                f"{element} is a core element — suspicion of sepsis IS "
                "the indication; there is no waiver for not looking")
        c = self._c(patient_id)
        if element in c["elements"]:
            raise SepsisError(f"{element} already completed — a waiver "
                              "cannot un-happen it")
        if element in c["waivers"]:
            raise SepsisError(f"{element} already waived")
        if not reason:
            raise SepsisError("a waiver without a reason is a silent "
                              "skip — the ledger does not do silent")
        c["waivers"][element] = {"hour": hour, "by": by,
                                 "reason": reason}
        return dict(c["waivers"][element])

    # ---- status and breaches ----
    def _element_state(self, c, element, as_of):
        if element in c["elements"]:
            e = c["elements"][element]
            return {"element": element, "state": "met" if e["met"]
                    else "breached-late", **e}
        if element in c["waivers"]:
            return {"element": element, "state": "waived",
                    **c["waivers"][element]}
        due_by = c["zero_hour"] + BUNDLE_ELEMENTS[element]
        breached = as_of > due_by
        return {"element": element, "state": "breached" if breached
                else "pending", "due_by": due_by,
                "overdue_hours": (as_of - due_by) if breached else 0}

    def status(self, patient_id, as_of):
        c = self._c(patient_id)
        return {el: self._element_state(c, el, as_of)
                for el in BUNDLE_ELEMENTS}

    def breaches(self, as_of):
        """Every uncompleted, unwaived element past its deadline —
        overdue-first across all running clocks."""
        out = []
        for pid, c in self.clocks.items():
            for el in BUNDLE_ELEMENTS:
                st = self._element_state(c, el, as_of)
                if st["state"] == "breached":
                    out.append({"patient_id": pid, **st})
        out.sort(key=lambda x: (-x["overdue_hours"], x["patient_id"]))
        return out

    # ---- the report ----
    def report(self, as_of):
        """Per-element met-rates with denominators, all-or-nothing
        bundle compliance, and median time-to-antibiotics — the number
        that actually tracks mortality."""
        per_element = {}
        for el in BUNDLE_ELEMENTS:
            applicable, met = 0, 0
            for c in self.clocks.values():
                st = self._element_state(c, el, as_of)
                if st["state"] == "waived":
                    continue
                applicable += 1
                if st["state"] == "met":
                    met += 1
            per_element[el] = {
                "applicable": applicable, "met": met,
                "met_rate": (round(met / applicable, 3)
                             if applicable else None)}
        all_or_nothing, passed = 0, 0
        for c in self.clocks.values():
            states = [self._element_state(c, el, as_of)
                      for el in BUNDLE_ELEMENTS]
            done = [s for s in states if s["state"] != "pending"]
            all_or_nothing += 1
            if all(s["state"] in ("met", "waived") for s in done) \
                    and not any(s["state"].startswith("breached")
                                for s in states):
                passed += 1
        tta = sorted(c["elements"]["broad-spectrum-antibiotics"]
                     ["elapsed_hours"]
                     for c in self.clocks.values()
                     if "broad-spectrum-antibiotics" in c["elements"])
        median_tta = None
        if tta:
            mid = len(tta) // 2
            median_tta = (tta[mid] if len(tta) % 2
                          else (tta[mid - 1] + tta[mid]) / 2)
        violations = sum(
            1 for c in self.clocks.values()
            for e in c["elements"].values()
            if e["sequence_violation"])
        return {"clocks": len(self.clocks),
                "per_element": per_element,
                "all_or_nothing": {"n": all_or_nothing, "met": passed,
                                   "rate": (round(passed / all_or_nothing, 3)
                                            if all_or_nothing else None)},
                "median_hours_to_antibiotics": median_tta,
                "sequence_violations": violations,
                "bundle_version": SEPSIS_BUNDLE_VERSION,
                "note": "the bundle is all-or-nothing: 5 of 6 elements "
                        "on time is a FAIL — sepsis does not grade on "
                        "a curve; denominators shown throughout"}

    def _c(self, pid):
        if pid not in self.clocks:
            raise SepsisError(f"no sepsis clock for {pid}")
        return self.clocks[pid]
