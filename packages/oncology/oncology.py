"""oncology.py — chemotherapy regimens, cycles, and toxicity tracking.

Chemotherapy is where dosing errors kill fastest and where governance
matters most: every regimen is a named protocol, every cycle is gated
on the previous one's toxicity, and every dose is capped against the
patient's measured body surface area.

Doctrine:
- REGIMENS ARE RATIFIED PROTOCOLS: a regimen is an entry (name, drugs,
  doses per m², cycle length, max cycles) that a named oncologist
  ratifies. No regimen, no chemotherapy.
- BSA IS COMPUTED, NOT ESTIMATED: height and weight produce BSA via
  Mosteller; the dose is capped at the protocol's per-m² maximum and the
  absolute ceiling.
- TOXICITY GATES THE NEXT CYCLE: a new cycle requires the previous
  cycle's toxicity graded (CTCAE-style) and the day's blood counts
  within protocol limits. Neutrophils < 1.0 refuse cycle start.
- EVERY CYCLE IS A RECORD: what was given, by whom, when, and what the
  bloods were — append-only.
"""

import hashlib
import json
import math

VERSION = "oncology@1.0.0"

CTCAE_GRADES = (0, 1, 2, 3, 4, 5)

# protocol registry — entries, not branches
REGIMENS = {}


class Error(Exception):
    pass


def register_regimen(name, *, drugs, cycle_length_days, max_cycles,
                     ratified_by, notes=None):
    """drugs: [{name, mg_per_m2, absolute_max_mg}]"""
    if name in REGIMENS:
        raise Error(f"regimen {name} already registered")
    if not ratified_by:
        raise Error("a regimen names its ratifying oncologist")
    if not drugs:
        raise Error("a regimen names its drugs")
    for d in drugs:
        for k in ("name", "mg_per_m2", "absolute_max_mg"):
            if k not in d:
                raise Error(f"drug entry missing {k}")
    if cycle_length_days <= 0 or max_cycles <= 0:
        raise Error("cycle length and max cycles must be positive")
    REGIMENS[name] = {"name": name, "drugs": drugs,
                      "cycle_length_days": cycle_length_days,
                      "max_cycles": max_cycles,
                      "ratified_by": ratified_by, "notes": notes}
    return REGIMENS[name]


def bsa_mosteller(height_cm, weight_kg):
    """The one home for BSA math."""
    if not (50.0 <= height_cm <= 250.0):
        raise Error(f"height {height_cm}cm implausible")
    if not (2.0 <= weight_kg <= 400.0):
        raise Error(f"weight {weight_kg}kg implausible")
    return math.sqrt(height_cm * weight_kg / 3600.0)


class ChemoCycle:
    """One cycle of one regimen for one patient."""

    def __init__(self, cycle_id, patient_ref, regimen_name, cycle_number,
                 *, height_cm, weight_kg, day):
        regimen = REGIMENS.get(regimen_name)
        if regimen is None:
            raise Error(f"unknown regimen: {regimen_name}")
        if cycle_number < 1 or cycle_number > regimen["max_cycles"]:
            raise Error(
                f"cycle {cycle_number} outside protocol 1–"
                f"{regimen['max_cycles']}")
        self.cycle_id = cycle_id
        self.patient_ref = patient_ref
        self.regimen = regimen
        self.cycle_number = cycle_number
        self.day = day
        self.bsa = bsa_mosteller(height_cm, weight_kg)
        self.height_cm = height_cm
        self.weight_kg = weight_kg
        self.doses = []
        self.toxicity = None
        self.bloods = None
        self.administered = False

    def _check_gate(self, bloods, previous_toxicity_grade):
        """Gate: bloods + prior toxicity must be within limits."""
        if bloods is None:
            raise Error("no cycle without the day's blood counts")
        for k in ("neutrophils", "platelets", "haemoglobin"):
            if k not in bloods:
                raise Error(f"bloods missing {k}")
        if bloods["neutrophils"] < 1.0:
            raise Error(
                f"neutrophils {bloods['neutrophils']} < 1.0 — "
                "cycle refused, not delayed-silently")
        if bloods["platelets"] < 50.0:
            raise Error(
                f"platelets {bloods['platelets']} < 50 — cycle refused")
        if previous_toxicity_grade is not None and previous_toxicity_grade >= 3:
            raise Error(
                f"grade {previous_toxicity_grade} toxicity on prior "
                "cycle — oncologist must dose-reduce before this cycle")

    def compute_doses(self, *, bloods, previous_toxicity_grade=None):
        self._check_gate(bloods, previous_toxicity_grade)
        self.bloods = bloods
        self.doses = []
        for d in self.regimen["drugs"]:
            computed = d["mg_per_m2"] * self.bsa
            capped = min(computed, d["absolute_max_mg"])
            self.doses.append({
                "drug": d["name"],
                "mg_per_m2": d["mg_per_m2"],
                "computed_mg": round(computed, 1),
                "administered_mg": round(capped, 1),
                "capped": capped < computed,
            })
        return self.doses

    def record_administration(self, *, by, nurse_witness):
        if not self.doses:
            raise Error("no doses computed — the gate has not passed")
        if self.administered:
            raise Error("cycle already administered")
        if not by:
            raise Error("administration names the nurse")
        if not nurse_witness or nurse_witness == by:
            raise Error("chemotherapy is witnessed by a second person")
        self.administered = True
        self.administered_by = by
        self.nurse_witness = nurse_witness

    def record_toxicity(self, grade, *, assessed_by, day):
        if grade not in CTCAE_GRADES:
            raise Error(f"CTCAE grade must be 0–5, got {grade}")
        if not assessed_by:
            raise Error("toxicity names its assessor")
        self.toxicity = {"grade": grade, "by": assessed_by, "day": day}


class OncologyCourse:
    """The full chemotherapy course for one patient."""

    def __init__(self, course_id, patient_ref, regimen_name, *,
                 oncologist, diagnosis):
        if regimen_name not in REGIMENS:
            raise Error(f"unknown regimen: {regimen_name}")
        if not oncologist:
            raise Error("the course names its oncologist")
        if not (diagnosis and diagnosis.strip()):
            raise Error("the diagnosis is stated")
        self.course_id = course_id
        self.patient_ref = patient_ref
        self.regimen_name = regimen_name
        self.oncologist = oncologist
        self.diagnosis = diagnosis
        self.cycles = []

    def plan_cycle(self, cycle_number, *, height_cm, weight_kg, day,
                   bloods, previous_toxicity_grade=None):
        if self.cycles and cycle_number <= max(c.cycle_number
                                               for c in self.cycles):
            raise Error("cycles are sequential — no repeats")
        cid = f"{self.course_id}-C{cycle_number}"
        c = ChemoCycle(cid, self.patient_ref, self.regimen_name,
                       cycle_number, height_cm=height_cm,
                       weight_kg=weight_kg, day=day)
        c.compute_doses(bloods=bloods,
                        previous_toxicity_grade=previous_toxicity_grade)
        self.cycles.append(c)
        return c

    def course_summary(self):
        last_tox = None
        for c in reversed(self.cycles):
            if c.toxicity:
                last_tox = c.toxicity
                break
        body = {"course_id": self.course_id,
                "patient_ref": self.patient_ref,
                "regimen": self.regimen_name,
                "cycles_planned": len(self.cycles),
                "cycles_given": sum(1 for c in self.cycles
                                    if c.administered),
                "last_toxicity": last_tox, "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
