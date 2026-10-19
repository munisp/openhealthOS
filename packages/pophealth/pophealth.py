"""Population health (ADR-0034) — OMOP-lite mapping, cohorts, care gaps,
predictive risk. Stdlib-only; reads FHIR R4 bundles (the SHR's native
shape), writes JSONL tables.

OMOP CDM: the common data model is a *schema convention*, not licensed
code — we map FHIR resources onto an OMOP-shaped lite layout with our
own concept namespace (OH:* local concepts alongside LOINC/SNOMED
source codes, exported as concepts.json so a full OHDSI vocabulary
load can replace it without touching the pipeline). Production swap:
the same JSONL tables become Parquet on the lakehouse silver tier and
the analytics keep working — the table shapes are the contract.

Predictive risk: RULES-FIRST, honestly labelled. The default scorers
below are directionally correct heuristics (age, blood pressure, BMI,
smoking, diabetes, family history, activity) with coefficients chosen
from published risk-factor direction — they are NOT validated clinical
instruments and every score carries "model": "heuristic-1". The swap
point is documented: when the ml registry holds a validated champion,
`risk_scores()` reads it instead (same interface as
ml/openhealth_ml.bridge.scorer_for) and the label changes. Never
present a heuristic score as a diagnosis; it exists to order outreach
lists and to surface prevention plan items.
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timezone

# ---------------------------------------------------------------- concepts
# Local concept namespace; a full OMOP vocabulary load replaces this map.
CONCEPTS = {
    "8480-6": 1001,   # LOINC systolic BP
    "8462-4": 1002,   # LOINC diastolic BP
    "4548-4": 1003,   # LOINC HbA1c
    "29463-7": 1004,  # LOINC body weight
    "8302-2": 1005,   # LOINC body height
    "8867-4": 1006,   # LOINC heart rate
    "55423-8": 1007,  # LOINC steps
    "39156-5": 1008,  # LOINC BMI
    "E11": 2001,      # ICD-10 type-2 diabetes
    "I10": 2002,      # ICD-10 essential hypertension
    "Z34": 2003,      # ICD-10 pregnancy supervision
    "OH-CERVICAL-SCREEN": 3001,
    "OH-IMMUNIZATION": 3002,
    "OH-NUTRITION-LOG": 3003,
    "OH-WORKOUT": 3004,
    "OH-SLEEP": 3005,
}

GENDER = {"male": 8507, "female": 8532}


def _code_of(resource):
    """First coding code of a CodeableConcept-ish resource."""
    cc = resource.get("code") or {}
    codings = cc.get("coding") or []
    return codings[0].get("code") if codings else None


def _patient_ref(resource):
    ref = (resource.get("subject") or resource.get("patient") or {}).get("reference", "")
    return ref.split("/")[-1] if ref else None


def _day(resource, *keys):
    for k in keys:
        v = resource.get(k)
        if isinstance(v, str) and v:
            return v[:10]
        if isinstance(v, dict) and v.get("start"):
            return v["start"][:10]
    return None


# ---------------------------------------------------------------- OMOP-lite
def to_omop(bundle):
    """FHIR bundle -> OMOP-shaped lite tables (lists of dicts)."""
    tables = {t: [] for t in ("person", "visit_occurrence", "condition_occurrence",
                              "measurement", "drug_exposure", "observation")}
    periods = {}
    for r in bundle:
        rt = r.get("resourceType")
        pid = _patient_ref(r) or (r.get("id") if rt == "Patient" else None)
        day = _day(r, "effectiveDateTime", "onsetDateTime", "authoredOn",
                   "period", "occurrenceDateTime")
        if pid and day:
            p = periods.setdefault(pid, [day, day])
            p[0], p[1] = min(p[0], day), max(p[1], day)
        if rt == "Patient":
            bd = r.get("birthDate", "")
            tables["person"].append({
                "person_id": r.get("id"),
                "year_of_birth": int(bd[:4]) if bd[:4].isdigit() else None,
                "gender_concept_id": GENDER.get((r.get("gender") or "").lower()),
                "person_source_value": r.get("id"),
            })
        elif rt == "Encounter":
            tables["visit_occurrence"].append({
                "visit_occurrence_id": r.get("id"), "person_id": pid,
                "visit_start_date": day,
                "visit_type_concept_id": 9202 if r.get("class") else 0})
        elif rt == "Condition":
            code = _code_of(r)
            tables["condition_occurrence"].append({
                "condition_occurrence_id": r.get("id"), "person_id": pid,
                "condition_concept_id": CONCEPTS.get(code, 0),
                "condition_source_value": code,
                "condition_start_date": day})
        elif rt == "Observation":
            code = _code_of(r)
            vq = r.get("valueQuantity") or {}
            row = {"obs_id": r.get("id"), "person_id": pid,
                   "concept_id": CONCEPTS.get(code, 0),
                   "source_value": code, "date": day}
            if isinstance(vq.get("value"), (int, float)):
                tables["measurement"].append({**row, "value_as_number": vq["value"],
                                              "unit": vq.get("unit")})
            else:
                tables["observation"].append(
                    {**row, "value_as_string": r.get("valueString")
                     or ((r.get("valueCodeableConcept") or {}).get("text"))})
        elif rt in ("MedicationRequest", "MedicationAdministration"):
            code = _code_of(r.get("medicationCodeableConcept") or {})
            tables["drug_exposure"].append({
                "drug_exposure_id": r.get("id"), "person_id": pid,
                "drug_concept_id": CONCEPTS.get(code, 0),
                "drug_source_value": code, "drug_exposure_start_date": day})
    tables["observation_period"] = [
        {"person_id": pid, "observation_period_start_date": p[0],
         "observation_period_end_date": p[1]}
        for pid, p in sorted(periods.items())]
    return tables


def write_omop(tables, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    for name, rows in tables.items():
        with open(os.path.join(out_dir, name + ".jsonl"), "w") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True) + "\n")
    with open(os.path.join(out_dir, "concepts.json"), "w") as fh:
        json.dump({"namespace": "OH-lite", "concepts": CONCEPTS}, fh, indent=2)
    return {k: len(v) for k, v in tables.items()}


# ---------------------------------------------------------------- cohorts
def _icd_prefix(code, prefix):
    return bool(code) and code.upper().startswith(prefix.upper())


COHORT_RULES = {
    "diabetes": lambda p, conds, _m: any(
        _icd_prefix(c["condition_source_value"], "E11") for c in conds),
    "hypertension": lambda p, conds, _m: any(
        _icd_prefix(c["condition_source_value"], "I10") for c in conds),
    "anc": lambda p, conds, _m: any(
        _icd_prefix(c["condition_source_value"], "Z34") for c in conds),
    "under5": lambda p, _c, _m: (
        p.get("year_of_birth") is not None
        and date.today().year - p["year_of_birth"] < 5),
    "cvd_watch": lambda p, conds, meas: (
        (p.get("year_of_birth") is not None
         and date.today().year - p["year_of_birth"] >= 40)
        or any(_icd_prefix(c["condition_source_value"], "I10") for c in conds)),
}


def build_cohorts(tables):
    """Registry-style cohorts from OMOP-lite tables."""
    persons = {p["person_id"]: p for p in tables["person"]}
    conds = {}
    meas = {}
    for c in tables["condition_occurrence"]:
        conds.setdefault(c["person_id"], []).append(c)
    for m in tables["measurement"]:
        meas.setdefault(m["person_id"], []).append(m)
    out = {name: [] for name in COHORT_RULES}
    for pid, p in persons.items():
        for name, rule in COHORT_RULES.items():
            if rule(p, conds.get(pid, []), meas.get(pid, [])):
                out[name].append(pid)
    return {k: sorted(v) for k, v in out.items()}


# ---------------------------------------------------------------- care gaps
# Rules as data: who it applies to, which observation closes it, how
# often it is due. Months are calendar-month approximations (30d).
CARE_GAP_RULES = [
    {"id": "bp-check", "label": "Blood pressure check",
     "applies": {"min_age": 18},
     "closes_with_concepts": [1001], "every_months": 12},
    {"id": "hba1c", "label": "HbA1c (diabetes control)",
     "applies": {"cohort": "diabetes"},
     "closes_with_concepts": [1003], "every_months": 6},
    {"id": "cervical-screen", "label": "Cervical cancer screening",
     "applies": {"sex": "female", "min_age": 25, "max_age": 49},
     "closes_with_concepts": [3001], "every_months": 36},
    {"id": "immunization", "label": "Routine immunization visit",
     "applies": {"cohort": "under5"},
     "closes_with_concepts": [3002], "every_months": 6},
    {"id": "anc-visit", "label": "Antenatal visit",
     "applies": {"cohort": "anc"},
     "closes_with_concepts": [], "closes_with_visit": True, "every_months": 1},
]


def _age(person, today):
    yob = person.get("year_of_birth")
    return (today.year - yob) if yob else None


def _months_since(day, today):
    if not day:
        return None
    try:
        d = date.fromisoformat(day[:10])
    except ValueError:
        return None
    return (today - d).days / 30.44


def care_gaps(tables, cohorts, today=None):
    """Per-patient overdue preventive items."""
    today = today or date.today()
    persons = {p["person_id"]: p for p in tables["person"]}
    last_meas = {}
    for m in tables["measurement"]:
        pid, c = m["person_id"], m["concept_id"]
        if m.get("date"):
            last_meas[(pid, c)] = max(last_meas.get((pid, c), ""), m["date"])
    for o in tables["observation"]:
        pid, c = o["person_id"], o["concept_id"]
        if o.get("date"):
            last_meas[(pid, c)] = max(last_meas.get((pid, c), ""), o["date"])
    last_visit = {}
    for v in tables["visit_occurrence"]:
        pid = v["person_id"]
        if v.get("visit_start_date"):
            last_visit[pid] = max(last_visit.get(pid, ""), v["visit_start_date"])

    gaps = {}
    for pid, p in persons.items():
        age = _age(p, today)
        sex = {8507: "male", 8532: "female"}.get(p.get("gender_concept_id"))
        mine = []
        for rule in CARE_GAP_RULES:
            a = rule["applies"]
            if a.get("min_age") is not None and (age is None or age < a["min_age"]):
                continue
            if a.get("max_age") is not None and (age is None or age > a["max_age"]):
                continue
            if a.get("sex") and sex != a["sex"]:
                continue
            if a.get("cohort") and pid not in cohorts.get(a["cohort"], []):
                continue
            if rule.get("closes_with_visit"):
                last = last_visit.get(pid)
            else:
                last = max((last_meas.get((pid, c), "")
                            for c in rule["closes_with_concepts"]), default="") or None
            months = _months_since(last, today)
            if months is None or months >= rule["every_months"]:
                mine.append({"gap": rule["id"], "label": rule["label"],
                             "last_done": last,
                             "months_since": round(months, 1) if months is not None else None,
                             "due_every_months": rule["every_months"]})
        if mine:
            gaps[pid] = mine
    return gaps


# ---------------------------------------------------------------- predictive risk
def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def _latest(meas, concept_id):
    vals = [m for m in meas if m["concept_id"] == concept_id and m.get("date")]
    return max(vals, key=lambda m: m["date"])["value_as_number"] if vals else None


def risk_scores(tables, cohorts):
    """Heuristic cardiovascular + diabetes risk per person.

    HEURISTIC-1: directionally correct weights, not a validated clinical
    score. Output is banded (low/moderate/high) and labelled; use it to
    order outreach lists, never to diagnose. Swap point: a validated
    champion in the ml registry replaces this function wholesale.
    """
    persons = {p["person_id"]: p for p in tables["person"]}
    meas = {}
    for m in tables["measurement"]:
        meas.setdefault(m["person_id"], []).append(m)
    out = {}
    for pid, p in persons.items():
        pm = meas.get(pid, [])
        age = _age(p, date.today()) or 40
        sbp = _latest(pm, 1001) or 120
        bmi = _latest(pm, 1008)
        if bmi is None:
            w, h = _latest(pm, 1004), _latest(pm, 1005)
            bmi = w / ((h / 100) ** 2) if w and h else 24
        diabetic = pid in cohorts.get("diabetes", [])
        hypertensive = pid in cohorts.get("hypertension", [])
        # transparent linear predictor; intercepts tuned so a healthy
        # 30-year-old lands low, an old hypertensive diabetic lands high
        cvd_z = (-5.2 + 0.045 * age + 0.018 * (sbp - 120)
                 + 0.06 * (bmi - 24) + 0.9 * diabetic + 0.7 * hypertensive)
        dm_z = (-4.6 + 0.05 * age + 0.11 * (bmi - 24) + 0.5 * hypertensive)
        cvd, dm = _sigmoid(cvd_z), _sigmoid(dm_z)
        band = lambda x: "high" if x >= 0.2 else "moderate" if x >= 0.1 else "low"
        out[pid] = {
            "model": "heuristic-1",
            "cvd_risk": round(cvd, 3), "cvd_band": band(cvd),
            "diabetes_risk": round(dm, 3), "diabetes_band": band(dm),
            "inputs": {"age": age, "sbp": sbp, "bmi": round(bmi, 1),
                       "diabetic": diabetic, "hypertensive": hypertensive},
        }
    return out


# ---------------------------------------------------------------- prevention plan
def prevention_plan(tables, cohorts, gaps, risks, patient_id):
    """One patient's preventive plan: overdue gaps + risk-driven advice.
    Wellness data (steps, workouts, nutrition logs) nudges the advice —
    the record the patient carries on their phone feeds their own plan."""
    plan = {"patient_id": patient_id, "overdue": gaps.get(patient_id, []),
            "risk": risks.get(patient_id)}
    pid_meas = [m for m in tables["measurement"] if m["person_id"] == patient_id]
    steps = [m for m in pid_meas if m["concept_id"] == 1007]
    workouts = [m for m in pid_meas if m["concept_id"] == 3004]
    recs = []
    r = risks.get(patient_id) or {}
    if r.get("cvd_band") in ("moderate", "high"):
        recs.append("Blood pressure and lifestyle review at next visit")
    if r.get("diabetes_band") in ("moderate", "high") and patient_id not in cohorts.get("diabetes", []):
        recs.append("Fasting glucose or HbA1c screening advised")
    if steps:
        avg = sum(m["value_as_number"] for m in steps[-14:]) / len(steps[-14:])
        plan["avg_daily_steps_14d"] = round(avg)
        if avg < 5000:
            recs.append("Activity is below 5,000 steps/day — start with a 20-minute daily walk")
    else:
        recs.append("Connect a step counter (phone or watch) to track activity")
    if not workouts:
        recs.append("Log or sync workouts — 150 minutes/week is the prevention target")
    if not any(m["concept_id"] == 3003 for m in
               [o for o in tables["observation"] if o["person_id"] == patient_id]):
        recs.append("Try the nutrition log — even 3 days a week shows patterns")
    for g in gaps.get(patient_id, []):
        recs.append(f"Due: {g['label']}")
    plan["recommendations"] = recs
    return plan


# ---------------------------------------------------------------- CLI
def load_bundle(path):
    with open(path) as fh:
        doc = json.load(fh)
    if doc.get("resourceType") == "Bundle":
        return [(e.get("resource") or {}) for e in doc.get("entry", [])]
    if isinstance(doc, list):
        return doc
    return [doc]


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="population health (ADR-0034)")
    ap.add_argument("op", choices=["omop", "cohorts", "gaps", "risk", "plan", "all"])
    ap.add_argument("--bundle", required=True, help="FHIR Bundle JSON (SHR export)")
    ap.add_argument("--patient", help="plan: patient id")
    ap.add_argument("--out", help="omop/all: output dir for JSONL tables")
    args = ap.parse_args(argv)

    bundle = load_bundle(args.bundle)
    tables = to_omop(bundle)
    cohorts = build_cohorts(tables)
    result = {}
    if args.op in ("omop", "all"):
        counts = write_omop(tables, args.out or "omop_out") if args.op == "omop" or args.out else {}
        result["omop"] = counts or {k: len(v) for k, v in tables.items()}
    if args.op in ("cohorts", "all", "gaps", "risk", "plan"):
        result["cohorts"] = {k: len(v) for k, v in cohorts.items()}
    if args.op in ("gaps", "all", "plan"):
        result["gaps"] = care_gaps(tables, cohorts)
    if args.op in ("risk", "all", "plan"):
        result["risk"] = risk_scores(tables, cohorts)
    if args.op == "plan":
        if not args.patient:
            ap.error("plan needs --patient")
        print(json.dumps(
            prevention_plan(tables, cohorts, result["gaps"], result["risk"],
                            args.patient), indent=2, sort_keys=True))
        return 0
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
