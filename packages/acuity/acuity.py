"""acuity.py — APACHE-II-shaped acute physiology scoring, for
benchmarking units honestly.

Mortality without acuity adjustment is a fiction: a unit that takes
the sickest patients SHOULD lose more of them. This module scores
acuity at admission (12 physiology variables + age + chronic health,
band tables shaped on the published APACHE II conventions, re-typed
as versioned data) and then reports mortality STRATIFIED by acuity
band with every denominator shown.

Doctrine:
  - absence is not a value: a missing physiology variable REFUSES —
    a partial acuity score is a wrong score wearing a smaller number
  - oxygenation follows the published rule: FiO2 >= 0.5 scores the
    A-a gradient, below that the PaO2 — supplying both and letting
    the code guess is how audits find lies
  - pH is scored from arterial pH, or from serum bicarbonate when no
    gas exists — explicitly, on the record
  - we do NOT emit a predicted-mortality SMR: that requires the
    diagnostic-category coefficients, which we do not have and will
    not invent. Stratified mortality with denominators is the honest
    version.
"""

ACUITY_VERSION = "apache-ii-shaped@1.0.0"

# band tables: list of (lo, hi, points) with None = open-ended;
# shaped on the published APACHE II (1985) physiology bands.
_TEMP = [(41, None, 4), (39, 41, 3), (38.5, 39, 1), (36, 38.5, 0),
         (34, 36, 1), (32, 34, 2), (30, 32, 3), (None, 30, 4)]
_MAP = [(160, None, 4), (130, 160, 3), (110, 130, 2), (70, 110, 0),
        (50, 70, 2), (None, 50, 4)]
_HR = [(180, None, 4), (140, 180, 3), (110, 140, 2), (70, 110, 0),
       (55, 70, 2), (40, 55, 3), (None, 40, 4)]
_RR = [(50, None, 4), (35, 50, 3), (25, 35, 1), (12, 25, 0),
       (10, 12, 1), (6, 10, 2), (None, 6, 4)]
_AADO2 = [(500, None, 4), (350, 500, 3), (200, 350, 2), (None, 200, 0)]
_PAO2 = [(70, None, 0), (61, 70, 1), (55, 61, 3), (None, 55, 4)]
_PH = [(7.7, None, 4), (7.6, 7.7, 3), (7.5, 7.6, 1),
       (7.33, 7.5, 0), (7.25, 7.33, 2), (7.15, 7.25, 3),
       (None, 7.15, 4)]
_HCO3 = [(52, None, 4), (41, 52, 3), (32, 41, 1), (22, 32, 0),
         (18, 22, 2), (15, 18, 3), (None, 15, 4)]
_NA = [(180, None, 4), (160, 180, 3), (155, 160, 2), (150, 155, 1),
       (130, 150, 0), (120, 130, 2), (111, 120, 3), (None, 111, 4)]
_K = [(7, None, 4), (6, 7, 3), (5.5, 6, 1), (3.5, 5.5, 0),
      (3, 3.5, 1), (2.5, 3, 2), (None, 2.5, 4)]
_CREAT = [(3.5, None, 4), (2, 3.5, 3), (1.5, 2, 2), (0.6, 1.5, 0),
          (None, 0.6, 2)]
_HCT = [(60, None, 4), (50, 60, 2), (46, 50, 1), (30, 46, 0),
        (20, 30, 2), (None, 20, 4)]
_WBC = [(40, None, 4), (20, 40, 2), (15, 20, 1), (3, 15, 0),
        (1, 3, 2), (None, 1, 4)]
_AGE = [(75, None, 6), (65, 75, 5), (55, 65, 3), (45, 55, 2),
        (None, 45, 0)]
CHRONIC_HEALTH_POINTS = {"nonsurgical-or-emergency": 5,
                         "elective-postop": 2}


class AcuityError(Exception):
    pass


def _band(table, value, name):
    for lo, hi, pts in table:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return pts
    raise AcuityError(f"{name}={value} falls outside every band — "
                      "check the unit, don't force the number")


def apache2(*, temp_c, map_mmhg, hr, rr, fio2, pao2=None, aado2=None,
            ph=None, hco3=None, na, k, creat_mgdl, arf=False, hct,
            wbc, gcs, age, chronic_health=None):
    """The admission acuity score. Every parameter required unless its
    documented alternative is supplied. GCS enters as 15 - GCS."""
    for name, val in (("temp_c", temp_c), ("map_mmhg", map_mmhg),
                      ("hr", hr), ("rr", rr), ("fio2", fio2),
                      ("na", na), ("k", k), ("creat_mgdl", creat_mgdl),
                      ("hct", hct), ("wbc", wbc), ("gcs", gcs),
                      ("age", age)):
        if val is None:
            raise AcuityError(
                f"{name} missing — absence is not a value; a partial "
                "acuity score is a wrong score wearing a smaller "
                "number")
    if not (0 < fio2 <= 1.0):
        raise AcuityError("fio2 must be a fraction (0.21-1.0)")
    if not (3 <= gcs <= 15):
        raise AcuityError(f"gcs {gcs} outside 3-15")
    if chronic_health not in (None, *CHRONIC_HEALTH_POINTS):
        raise AcuityError(f"unknown chronic health class: "
                          f"{chronic_health}")

    per = {}
    per["temperature"] = _band(_TEMP, temp_c, "temp_c")
    per["map"] = _band(_MAP, map_mmhg, "map_mmhg")
    per["heart-rate"] = _band(_HR, hr, "hr")
    per["respiratory-rate"] = _band(_RR, rr, "rr")

    # oxygenation: the published rule, no guessing
    if fio2 >= 0.5:
        if aado2 is None:
            raise AcuityError("fio2 >= 0.5 scores the A-a gradient — "
                              "supply aado2; a PaO2 here scores wrong")
        per["oxygenation"] = _band(_AADO2, aado2, "aado2")
        per["oxygenation_basis"] = "aado2"
    else:
        if pao2 is None:
            raise AcuityError("fio2 < 0.5 scores PaO2 — supply pao2")
        per["oxygenation"] = _band(_PAO2, pao2, "pao2")
        per["oxygenation_basis"] = "pao2"

    # acid-base: arterial pH, else serum bicarbonate — on the record
    if ph is not None:
        per["acid-base"] = _band(_PH, ph, "ph")
        per["acid_base_basis"] = "ph"
    elif hco3 is not None:
        per["acid-base"] = _band(_HCO3, hco3, "hco3")
        per["acid_base_basis"] = "hco3"
    else:
        raise AcuityError("no acid-base value — supply ph or hco3; "
                          "neither is a refusal to look")

    per["sodium"] = _band(_NA, na, "na")
    per["potassium"] = _band(_K, k, "k")
    creat_pts = _band(_CREAT, creat_mgdl, "creat_mgdl")
    if arf:
        creat_pts *= 2     # acute renal failure doubles renal points
    per["creatinine"] = creat_pts
    per["hematocrit"] = _band(_HCT, hct, "hct")
    per["wbc"] = _band(_WBC, wbc, "wbc")
    per["gcs"] = 15 - gcs

    aps = sum(v for k, v in per.items() if isinstance(v, int))
    age_points = _band(_AGE, age, "age")
    chronic_points = CHRONIC_HEALTH_POINTS.get(chronic_health, 0)
    return {"total": aps + age_points + chronic_points,
            "aps": aps, "age_points": age_points,
            "chronic_health_points": chronic_points,
            "per_variable": per,
            "acuity_version": ACUITY_VERSION}


ACUITY_BANDS = ((0, 10), (10, 20), (20, 30), (30, None))
OUTCOMES = ("survived", "died", "transferred")


class AcuityRegistry:
    """Admission scores + outcomes per unit → mortality stratified by
    acuity band, denominators shown. No invented SMR."""

    def __init__(self):
        self.units = {}      # unit -> {patient_id: record}

    def record_score(self, unit, patient_id, *, day, score):
        if not unit:
            raise AcuityError("a score belongs to a named unit")
        if not isinstance(score, dict) or "total" not in score:
            raise AcuityError("score must be the apache2() result — "
                              "a bare integer smuggles past every "
                              "refusal the scorer made")
        recs = self.units.setdefault(unit, {})
        if patient_id in recs:
            raise AcuityError(f"{patient_id} already scored in {unit}"
                              " — a rescore is a new admission, not "
                              "an edit")
        recs[patient_id] = {"day": day, "score": score["total"],
                            "outcome": None}
        return dict(recs[patient_id])

    def record_outcome(self, unit, patient_id, *, day, outcome):
        if outcome not in OUTCOMES:
            raise AcuityError(f"unknown outcome: {outcome}")
        rec = self._r(unit, patient_id)
        if rec["outcome"]:
            raise AcuityError("outcome already recorded — the ledger "
                              "does not revise the dead")
        rec["outcome"] = {"day": day, "outcome": outcome}
        return dict(rec)

    def mortality_by_band(self, unit):
        """Mortality per acuity band with denominators — the honest
        acuity adjustment. Transferred patients are excluded from the
        outcome columns but counted: hiding them would flatter the
        unit."""
        recs = self.units.get(unit, {})
        out = {"unit": unit, "scored": len(recs), "bands": []}
        for lo, hi in ACUITY_BANDS:
            members = [r for r in recs.values()
                       if r["score"] >= lo
                       and (hi is None or r["score"] < hi)]
            died = sum(1 for r in members
                       if r["outcome"] and
                       r["outcome"]["outcome"] == "died")
            discharged = [r for r in members if r["outcome"]]
            transferred = sum(1 for r in discharged
                              if r["outcome"]["outcome"]
                              == "transferred")
            closed = len(discharged) - transferred
            out["bands"].append({
                "band": f"{lo}-{hi if hi is not None else '+'}",
                "n": len(members), "died": died,
                "closed_outcomes": closed,
                "transferred_out": transferred,
                "mortality_rate": (round(died / closed, 3)
                                   if closed else None)})
        out["note"] = ("mortality is stratified, not adjusted into a "
                       "single number: a unit that takes the sickest "
                       "patients should be read band by band; every "
                       "rate shows its n")
        return out

    def _r(self, unit, pid):
        if pid not in self.units.get(unit, {}):
            raise AcuityError(f"no score for {pid} in {unit}")
        return self.units[unit][pid]
