"""derive.py — ratified clinical derivation registry + validation harness.

Derived variables (SOFA, KDIGO-AKI, MELD-Na, Charlson, Sepsis-3) decide
cohorts, escalation, and research conclusions — yet their definitions
live OUTSIDE the schema: item codes, time windows, missingness rules,
output grain. M4Bench (arXiv:2610.37459) measured what happens when
agents improvise those choices: specification completeness is nearly
the whole effect (operational spec alone +0.162 reward; delivery format
+0.024, indistinguishable from zero), and even WITH specs only 25/280
agent runs passed every strict check — errors concentrate in output
grain, not row selection (key F1 +0.003).

Doctrine:
  - A derivation is DECLARED, VERSIONED DATA: source items, window,
    missingness rule, output grain, value range, reference, caveats.
    An ad-hoc formula in a notebook is a derivation nobody can audit.
  - FOUR-EYES: a spec is proposed and ratified by different hands.
    compute() refuses unratified specs — a template score is a
    placeholder, not a number.
  - ABSENCE IS NOT A VALUE: missing inputs follow the spec's
    missingness rule, and the default rule is REFUSE. Public concept
    code that silently defaults missing SOFA components to zero is
    exactly the failure mode M4Bench's clinician reviewers flagged.
  - OUTPUT IS CHECKED, NOT TRUSTED: validate_output enforces key
    coverage, duplicate keys, value ranges, and grain — the checks
    M4Bench found agents failing even when their values were close.
  - GRAIN IS DECLARED: per-stay-per-day is not per-stay. A score
    without a grain is a number without a unit.
"""

from __future__ import annotations

import hashlib
import json
import math

VERSION = "derive@1.0.0"

GRAINS = ("per-stay", "per-stay-per-day", "per-encounter", "per-patient")
MISSINGNESS = ("refuse", "carry-forward", "zero-with-flag")


class DeriveError(Exception):
    pass


def _canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def fingerprint(spec):
    payload = {k: v for k, v in spec.items() if k != "status"}
    return hashlib.sha256(_canon(payload)).hexdigest()


# ---------------------------------------------------------------- registry

class DerivationRegistry:
    """Four-eyes registry of derivation specs (data, not code)."""

    def __init__(self):
        self._specs = {}

    def propose(self, spec, *, proposer):
        name = spec.get("name")
        if not name:
            raise DeriveError("spec needs a name")
        if not proposer:
            raise DeriveError("spec needs a proposer")
        for field in ("family", "version", "output_grain", "required_inputs",
                      "missingness", "value_range", "reference"):
            if field not in spec:
                raise DeriveError(f"spec missing field: {field}")
        if spec["output_grain"] not in GRAINS:
            raise DeriveError(f"unknown grain: {spec['output_grain']}")
        if spec["missingness"] not in MISSINGNESS:
            raise DeriveError(f"unknown missingness: {spec['missingness']}")
        key = (name, spec["version"])
        if key in self._specs:
            raise DeriveError(f"duplicate spec: {name}@{spec['version']}")
        spec = dict(spec, status="proposed", proposer=proposer)
        self._specs[key] = spec
        return fingerprint(spec)

    def ratify(self, name, version, *, approver):
        spec = self._specs.get((name, version))
        if spec is None:
            raise DeriveError(f"unknown spec: {name}@{version}")
        if not approver:
            raise DeriveError("ratification needs an approver")
        if approver == spec["proposer"]:
            raise DeriveError("the hand that proposes never ratifies")
        spec["status"] = "ratified"
        spec["approver"] = approver
        return fingerprint(spec)

    def get(self, name, version=None):
        cands = [s for (n, v), s in self._specs.items() if n == name
                 and (version is None or v == version)]
        if not cands:
            raise DeriveError(f"unknown derivation: {name}")
        return sorted(cands, key=lambda s: s["version"])[-1]

    def compute(self, name, inputs, *, version=None):
        spec = self.get(name, version)
        if spec["status"] != "ratified":
            raise DeriveError(
                f"derivation {name}@{spec['version']} is {spec['status']} — "
                "a template score is a placeholder, not a number")
        fn = _IMPLEMENTATIONS.get(name)
        if fn is None:
            raise DeriveError(f"no implementation for: {name}")
        result = fn(inputs, spec)
        lo, hi = spec["value_range"]
        total = result.get("value")
        if total is not None and not (lo <= total <= hi):
            raise DeriveError(
                f"{name} value {total} outside declared range [{lo},{hi}] — "
                "refusing to emit an impossible score")
        result["spec"] = f"{name}@{spec['version']}"
        result["spec_fingerprint"] = fingerprint(spec)
        result["grain"] = spec["output_grain"]
        return result


def _require(inputs, keys, spec):
    missing = [k for k in keys if inputs.get(k) is None]
    if missing and spec["missingness"] == "refuse":
        raise DeriveError(
            f"missing inputs {missing} — absence is not a value")
    return missing


# ---------------------------------------------------------------- SOFA
# Vincent et al., Intensive Care Med 1996. Bands as published.

def _sofa(inputs, spec):
    missing = _require(inputs, ("pao2", "fio2", "platelets", "bilirubin",
                                "map", "gcs", "creatinine"), spec)
    sub = {}
    if "pao2" not in missing and "fio2" not in missing:
        ratio = inputs["pao2"] / max(inputs["fio2"], 0.01)
        vent = bool(inputs.get("on_vent"))
        if ratio >= 400:
            sub["respiration"] = 0
        elif ratio >= 300:
            sub["respiration"] = 1
        elif ratio >= 200:
            sub["respiration"] = 3 if vent else 2
        elif ratio >= 100:
            sub["respiration"] = 4 if vent else 3
        else:
            sub["respiration"] = 4 if vent else 4
    plt = inputs.get("platelets")
    if plt is not None:
        sub["coagulation"] = (0 if plt >= 150 else 1 if plt >= 100
                              else 2 if plt >= 50 else 3 if plt >= 20 else 4)
    bili = inputs.get("bilirubin")
    if bili is not None:
        sub["liver"] = (0 if bili < 1.2 else 1 if bili < 2
                        else 2 if bili < 6 else 3 if bili < 12 else 4)
    # cardiovascular: MAP and vasopressor doses (mcg/kg/min)
    dopa = inputs.get("dopamine") or 0.0
    dobu = inputs.get("dobutamine") or 0.0
    epi = inputs.get("epinephrine") or 0.0
    norepi = inputs.get("norepinephrine") or 0.0
    mapv = inputs.get("map")
    if mapv is not None:
        if dopa > 15 or epi > 0.1 or norepi > 0.1:
            sub["cardiovascular"] = 4
        elif dopa > 5 or epi > 0 or norepi > 0:
            sub["cardiovascular"] = 3
        elif dopa > 0 or dobu > 0:
            sub["cardiovascular"] = 2
        elif mapv < 70:
            sub["cardiovascular"] = 1
        else:
            sub["cardiovascular"] = 0
    gcs = inputs.get("gcs")
    if gcs is not None:
        sub["cns"] = (0 if gcs >= 15 else 1 if gcs >= 13
                      else 2 if gcs >= 10 else 3 if gcs >= 6 else 4)
    cr = inputs.get("creatinine")
    uo = inputs.get("urine_output_ml_day")
    if cr is not None or uo is not None:
        renal = 0
        if cr is not None:
            renal_cr = (0 if cr < 1.2 else 1 if cr < 2
                        else 2 if cr < 3.5 else 3 if cr < 5 else 4)
        else:
            renal_cr = 0
        renal_uo = 0
        if uo is not None:
            renal_uo = 3 if uo < 500 else 0
            if uo < 200:
                renal_uo = 4
        sub["renal"] = max(renal_cr, renal_uo)
    flagged = [k for k in missing if k in ("pao2", "fio2", "platelets",
                                           "bilirubin", "map", "gcs",
                                           "creatinine")]
    total = sum(sub.values())
    out = {"value": total, "components": sub}
    if flagged and spec["missingness"] != "refuse":
        out["missing_components"] = sorted(flagged)
        out["note"] = ("missing components EXCLUDED, not zeroed — "
                       "the printed convention of defaulting missing "
                       "organs to 0 understates severity")
    return out


# ---------------------------------------------------------------- qSOFA

def _qsofa(inputs, spec):
    _require(inputs, ("rr", "gcs", "sbp"), spec)
    score = (int(inputs["rr"] >= 22) + int(inputs["gcs"] < 15)
             + int(inputs["sbp"] <= 100))
    return {"value": score, "components": {
        "rr>=22": inputs["rr"] >= 22,
        "altered-mentation": inputs["gcs"] < 15,
        "sbp<=100": inputs["sbp"] <= 100}}


# ---------------------------------------------------------------- KDIGO AKI
# KDIGO 2012: creatinine ratio vs baseline, +0.3 within 48h, urine
# output windows, dialysis. Baseline is REQUIRED — the single most
# common AKI misderivation (M4Bench: baseline-creatinine hierarchy is a
# highest-friction family) is inventing one.

def _kdigo_aki(inputs, spec):
    _require(inputs, ("baseline_creatinine",), spec)
    base = inputs["baseline_creatinine"]
    if base <= 0:
        raise DeriveError("baseline creatinine must be positive")
    stage = 0
    basis = []
    cr = inputs.get("creatinine")
    if cr is not None:
        ratio = cr / base
        if cr >= 4.0:
            stage, basis = 3, basis + ["creatinine >= 4.0 mg/dL"]
        elif ratio >= 3.0:
            stage, basis = max(stage, 3), basis + [f"ratio {ratio:.2f} >= 3.0"]
        elif ratio >= 2.0:
            stage, basis = max(stage, 2), basis + [f"ratio {ratio:.2f} >= 2.0"]
        elif ratio >= 1.5:
            stage, basis = max(stage, 1), basis + [f"ratio {ratio:.2f} >= 1.5"]
        if (inputs.get("cr_delta_48h") or 0.0) >= 0.3:
            stage, basis = max(stage, 1), basis + ["+0.3 mg/dL within 48h"]
    uo = inputs.get("urine_ml_kg_hr")
    hours = inputs.get("urine_window_hours")
    if uo is not None and hours is not None:
        if uo < 0.3 and hours >= 24:
            stage, basis = max(stage, 3), basis + ["UO <0.3 for >=24h"]
        elif uo < 0.5 and hours >= 12:
            stage, basis = max(stage, 2), basis + ["UO <0.5 for >=12h"]
        elif uo < 0.5 and hours >= 6:
            stage, basis = max(stage, 1), basis + ["UO <0.5 for 6-12h"]
    if inputs.get("anuria_hours", 0) >= 12:
        stage, basis = max(stage, 3), basis + ["anuria >= 12h"]
    if inputs.get("on_dialysis"):
        stage, basis = max(stage, 3), basis + ["renal replacement therapy"]
    return {"value": stage, "basis": basis or ["no criterion met"],
            "baseline_source": inputs.get("baseline_source", "declared")}


# ---------------------------------------------------------------- MELD-Na
# UNOS/OPTN 2016 revision. Lab floors 1.0, creatinine cap 4.0,
# dialysis (>=2 sessions or 24h CVVHD in prior week) sets Cr to 4.0,
# Na clamped 125-137, final clamp 6-40.

def _meld_na(inputs, spec):
    _require(inputs, ("bilirubin", "inr", "creatinine", "sodium"), spec)
    bili = max(inputs["bilirubin"], 1.0)
    inr = max(inputs["inr"], 1.0)
    cr = min(max(inputs["creatinine"], 1.0), 4.0)
    if inputs.get("on_dialysis"):
        cr = 4.0
    meld = (9.57 * math.log(cr) + 3.78 * math.log(bili)
            + 11.2 * math.log(inr) + 6.43)
    meld = round(meld)
    na = min(max(inputs["sodium"], 125), 137)
    meld_na = meld
    if meld > 11:
        meld_na = meld + 1.32 * (135 - na) - 0.033 * meld * (135 - na)
        meld_na = round(meld_na)
    return {"value": min(max(meld_na, 6), 40),
            "meld_raw": meld, "sodium_used": na,
            "note": "UNOS 2016 bounds applied (lab floors, Cr cap, Na clamp)"}


# ---------------------------------------------------------------- Charlson
# Quan et al. ICD-10 adaptation — STARTER SUBSET declared honestly;
# deployments load their full map. Weight table as data.

CHARLSON_WEIGHTS = {
    "myocardial-infarction": (1, ("I21", "I22", "I25.2")),
    "heart-failure": (1, ("I50",)),
    "peripheral-vascular": (1, ("I70", "I73.9")),
    "cerebrovascular": (1, ("I60", "I61", "I62", "I63", "I64", "I65",
                            "I66", "I67", "I68", "I69")),
    "dementia": (1, ("F00", "F01", "F02", "F03", "G30")),
    "copd": (1, ("J44",)),
    "rheumatic": (1, ("M05", "M06")),
    "peptic-ulcer": (1, ("K25", "K26", "K27", "K28")),
    "mild-liver": (1, ("B18", "K73", "K74")),
    "diabetes": (1, ("E10.9", "E11.9", "E13.9", "E14.9")),
    "hemiplegia": (2, ("G81", "G82")),
    "ckd-moderate-severe": (2, ("N18.3", "N18.4", "N18.5")),
    "diabetes-complicated": (2, ("E10.2", "E10.5", "E11.2", "E11.5")),
    "tumor": (2, ("C00", "C15", "C16", "C18", "C20", "C25", "C34",
                  "C50", "C61", "C71")),
    "leukemia": (2, ("C91", "C92", "C93", "C94", "C95")),
    "lymphoma": (2, ("C81", "C82", "C83", "C84", "C85")),
    "moderate-severe-liver": (3, ("K72", "K76.6", "I85.0")),
    "metastatic": (6, ("C77", "C78", "C79", "C80")),
    "aids": (6, ("B20", "B21", "B22", "B24")),
}


def _charlson(inputs, spec):
    _require(inputs, ("icd10_codes", "age"), spec)
    codes = [c.upper() for c in inputs["icd10_codes"]]
    age = inputs["age"]
    if not (0 <= age <= 130):
        raise DeriveError(f"implausible age: {age}")
    matched = []
    score = 0
    for condition, (weight, prefixes) in CHARLSON_WEIGHTS.items():
        if any(c.startswith(p) for c in codes for p in prefixes):
            matched.append(condition)
            score += weight
    age_points = 0 if age < 40 else min((age - 40) // 10 + 1, 4)
    return {"value": score + age_points, "conditions": sorted(matched),
            "age_points": age_points,
            "note": "Quan ICD-10 starter subset — unmapped codes are "
                    "absent, not zero-weight-by-fiat"}


# ------------------------------------------------- suspected infection
# Sepsis-3 convention (Seymour 2016): culture then antibiotics within
# 72h, or antibiotics then culture within 24h.

def _suspected_infection(inputs, spec):
    culture = inputs.get("culture_time")
    abx = inputs.get("antibiotic_time")
    if culture is None or abx is None:
        if spec["missingness"] == "refuse":
            raise DeriveError(
                "suspected infection needs BOTH culture and antibiotic "
                "timestamps — one without the other is not a suspicion")
        return {"value": 0, "note": "incomplete pair — not assessed"}
    delta = abx - culture
    suspected = (0 <= delta <= 72) or (-24 <= delta < 0)
    return {"value": int(suspected),
            "time_zero": min(culture, abx) if suspected else None,
            "basis": ("culture->abx within 72h" if 0 <= delta <= 72 else
                      "abx->culture within 24h" if -24 <= delta < 0 else
                      "outside both windows")}


# ---------------------------------------------------------------- Sepsis-3

def _sepsis3(inputs, spec):
    _require(inputs, ("suspected_infection", "sofa_total"), spec)
    si = inputs["suspected_infection"]
    sofa = inputs["sofa_total"]
    positive = bool(si) and sofa >= 2
    return {"value": int(positive),
            "basis": f"suspected-infection={bool(si)}, SOFA={sofa} "
                     f"({'>=2' if sofa >= 2 else '<2'})"}


_IMPLEMENTATIONS = {
    "sofa": _sofa, "qsofa": _qsofa, "kdigo-aki": _kdigo_aki,
    "meld-na": _meld_na, "charlson": _charlson,
    "suspected-infection": _suspected_infection, "sepsis-3": _sepsis3,
}


# ------------------------------------------------------- built-in specs

BUILTIN_SPECS = [
    {"name": "sofa", "family": "severity", "version": "1.0.0",
     "output_grain": "per-stay-per-day",
     "required_inputs": ["pao2", "fio2", "platelets", "bilirubin",
                         "map", "gcs", "creatinine"],
     "missingness": "refuse", "value_range": [0, 24],
     "reference": "Vincent 1996, Intensive Care Med 22:707",
     "caveats": ["public concept code defaults missing organs to zero — "
                 "this implementation refuses instead"]},
    {"name": "qsofa", "family": "severity", "version": "1.0.0",
     "output_grain": "per-encounter",
     "required_inputs": ["rr", "gcs", "sbp"],
     "missingness": "refuse", "value_range": [0, 3],
     "reference": "Singer 2016, JAMA 315:801 (Sepsis-3)",
     "caveats": ["screening tool, not a diagnosis"]},
    {"name": "kdigo-aki", "family": "organ-dysfunction", "version": "1.0.0",
     "output_grain": "per-stay",
     "required_inputs": ["baseline_creatinine"],
     "missingness": "refuse", "value_range": [0, 3],
     "reference": "KDIGO 2012, Kidney Int Suppl 2:1",
     "caveats": ["baseline creatinine must be measured or documented — "
                 "an estimated baseline is a stated deployment choice"]},
    {"name": "meld-na", "family": "organ-dysfunction", "version": "1.0.0",
     "output_grain": "per-encounter",
     "required_inputs": ["bilirubin", "inr", "creatinine", "sodium"],
     "missingness": "refuse", "value_range": [6, 40],
     "reference": "UNOS/OPTN 2016 revision",
     "caveats": ["allocation score, not a prognosis guarantee"]},
    {"name": "charlson", "family": "comorbidity", "version": "1.0.0",
     "output_grain": "per-patient",
     "required_inputs": ["icd10_codes", "age"],
     "missingness": "refuse", "value_range": [0, 37],
     "reference": "Quan 2005 ICD-10 adaptation (starter subset)",
     "caveats": ["starter subset — deployments load their full map"]},
    {"name": "suspected-infection", "family": "infection", "version": "1.0.0",
     "output_grain": "per-stay",
     "required_inputs": ["culture_time", "antibiotic_time"],
     "missingness": "refuse", "value_range": [0, 1],
     "reference": "Seymour 2016, JAMA 315:762",
     "caveats": ["retrospective research convention, not triage"]},
    {"name": "sepsis-3", "family": "infection", "version": "1.0.0",
     "output_grain": "per-stay",
     "required_inputs": ["suspected_infection", "sofa_total"],
     "missingness": "refuse", "value_range": [0, 1],
     "reference": "Singer 2016, JAMA 315:801",
     "caveats": ["requires ratified SOFA + suspected-infection upstream"]},
]


def load_builtins(registry, *, proposer):
    fps = {}
    for spec in BUILTIN_SPECS:
        fps[spec["name"]] = registry.propose(spec, proposer=proposer)
    return fps


# ---------------------------------------------------- validation harness

def validate_output(spec, rows, *, key_cols, expected_keys=None):
    """Strict acceptance checks for derivation output (M4Bench's harness
    discipline): keys, duplicates, coverage, grain, value range.
    A pass with silent gaps is a fail that hasn't been caught yet."""
    checks = []
    ok = True

    def check(name, passed, detail):
        nonlocal ok
        ok = ok and passed
        checks.append({"check": name, "ok": bool(passed), "detail": detail})

    seen = set()
    dupes = 0
    for row in rows:
        key = tuple(row.get(k) for k in key_cols)
        if key in seen:
            dupes += 1
        seen.add(key)
    check("no-duplicate-keys", dupes == 0,
          f"{dupes} duplicate keys over {len(rows)} rows")

    null_keys = sum(1 for row in rows
                    if any(row.get(k) is None for k in key_cols))
    check("keys-present", null_keys == 0, f"{null_keys} rows with null key")

    if expected_keys is not None:
        expected = {tuple(k) if isinstance(k, (list, tuple)) else (k,)
                    for k in expected_keys}
        missing = len(expected - seen)
        extra = len(seen - expected)
        check("coverage", missing == 0,
              f"{missing}/{len(expected)} expected keys absent")
        check("no-extra-keys", extra == 0, f"{extra} unexpected keys")

    lo, hi = spec["value_range"]
    out_of_range = sum(
        1 for row in rows
        if row.get("value") is not None
        and not (lo <= row["value"] <= hi))
    check("value-range", out_of_range == 0,
          f"{out_of_range} values outside [{lo},{hi}]")

    check("grain-declared", spec["output_grain"] in GRAINS,
          f"grain={spec['output_grain']}")

    return {"ok": ok, "spec": f"{spec['name']}@{spec['version']}",
            "rows": len(rows), "checks": checks}
