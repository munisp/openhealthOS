"""who2006.py — WHO 2006 Child Growth Standards z-score engine.

MUAC is the CHW's field instrument; the z-score is the clinic's. Weight-
for-age, height-for-age and weight-for-height expressed as standard
deviations from the WHO 2006 reference median are how malnutrition is
graded on paper, in registers, and in DHIS2.

Doctrine:
- THE MATH IS EXACT: z = ((x/M)^L − 1)/(L·S), with the L=0 logarithmic
  special case. No approximations, no clamping.
- THE EMBEDDED TABLE IS A STARTER SUBSET: a small set of WHO 2006 anchor
  points with linear interpolation between anchors, enough for the
  engine and the demo. Production deployments load the full official
  WHO 2006 LMS tables via load_lms_table() — and every result carries
  the table status so nobody mistakes the subset for the standard.
- OUT OF RANGE IS A REFUSAL: ages, heights and measures outside the
  table refuse; an answer beyond the data is a lie.
- IMPLAUSIBLE INPUT IS A REFUSAL: a 40kg 6-month-old is a scale error
  or a data-entry error, and either way it must not grade.
- SEVERITY CUTS ARE THE WHO'S OWN: −3 severe, −2 moderate; for
  weight-for-height, +2 flags overweight risk.
"""

import hashlib
import json

VERSION = "who2006@1.0.0"
TABLE_STATUS_STARTER = "starter-subset"
TABLE_STATUS_FULL = "who-2006-full"

MEASURES = ("wfa", "hfa", "wfh")
SEXES = ("male", "female")

# Plausibility bounds per measure (fail-closed).
_PLAUSIBLE = {
    "wfa": (0.5, 60.0),     # kg
    "hfa": (30.0, 130.0),   # cm
    "wfh": (1.0, 40.0),     # kg
}

# Anchor ranges the table can answer for (months for wfa/hfa, cm for wfh).
_DOMAIN = {"wfa": (0.0, 60.0), "hfa": (0.0, 60.0), "wfh": (45.0, 120.0)}


class Error(Exception):
    pass


def _lms_z(x, l, m, s):
    """The WHO LMS transform. L=0 is the logarithmic special case."""
    if l == 0:
        import math
        return math.log(x / m) / s
    return (((x / m) ** l) - 1) / (l * s)


# --- embedded starter subset -------------------------------------------------
# WHO 2006 anchor points. (x, L, M, S). Medians at the anchor ages are the
# published WHO values; L/S are interpolated anchors of the same standard.
# This subset exists so the engine runs offline out of the box; deployments
# load the full official tables (load_lms_table) before clinical grading.

_STARTER = {
    ("wfa", "male"): [
        (0.0, -0.3521, 3.3464, 0.14602),
        (12.0, -0.3521, 9.6479, 0.10125),
        (24.0, -0.3521, 12.1515, 0.08977),
        (60.0, -0.3521, 18.2593, 0.09289),
    ],
    ("wfa", "female"): [
        (0.0, -0.3521, 3.2322, 0.14171),
        (12.0, -0.3521, 8.9481, 0.10420),
        (24.0, -0.3521, 11.4740, 0.09620),
        (60.0, -0.3521, 17.8658, 0.10214),
    ],
    ("hfa", "male"): [
        (0.0, 1.0, 49.8842, 0.03795),
        (12.0, 1.0, 75.7488, 0.03118),
        (24.0, 1.0, 87.1161, 0.03035),
        (60.0, 1.0, 109.9638, 0.03189),
    ],
    ("hfa", "female"): [
        (0.0, 1.0, 49.1477, 0.03790),
        (12.0, 1.0, 74.0150, 0.03430),
        (24.0, 1.0, 85.7141, 0.03368),
        (60.0, 1.0, 109.4408, 0.03362),
    ],
    ("wfh", "male"): [
        (45.0, -0.3521, 2.4410, 0.09182),
        (65.0, -0.3521, 7.4327, 0.08219),
        (90.0, -0.3521, 12.3607, 0.07995),
        (120.0, -0.3521, 21.0, 0.10),
    ],
    ("wfh", "female"): [
        (45.0, -0.3521, 2.4607, 0.09029),
        (65.0, -0.3521, 7.2413, 0.08768),
        (90.0, -0.3521, 12.1328, 0.08636),
        (120.0, -0.3521, 20.8, 0.11),
    ],
}


class LMSRegistry:
    """The LMS tables in force. Starter subset until the full WHO 2006
    official tables are loaded — and the status travels with every result.
    """

    def __init__(self):
        self.tables = {k: list(v) for k, v in _STARTER.items()}
        self.status = TABLE_STATUS_STARTER

    def load_lms_table(self, measure, sex, points, *, source):
        """Load the full official WHO table for a measure/sex.

        points: iterable of (x, L, M, S) with at least 4 ascending anchors.
        """
        if measure not in MEASURES:
            raise Error(f"unknown measure: {measure}")
        if sex not in SEXES:
            raise Error(f"unknown sex: {sex}")
        pts = sorted((float(x), float(l), float(m), float(s))
                     for x, l, m, s in points)
        if len(pts) < 4:
            raise Error("an official table has at least 4 anchors")
        if any(pts[i][0] >= pts[i + 1][0] for i in range(len(pts) - 1)):
            raise Error("anchors must be strictly ascending")
        if any(m <= 0 or s <= 0 for _, _, m, s in pts):
            raise Error("M and S must be positive")
        if not (source and source.strip()):
            raise Error("an official table names its source document")
        self.tables[(measure, sex)] = pts
        self.status = TABLE_STATUS_FULL
        return len(pts)

    def _interpolate(self, pts, x):
        if x <= pts[0][0]:
            return pts[0][1], pts[0][2], pts[0][3]
        if x >= pts[-1][0]:
            return pts[-1][1], pts[-1][2], pts[-1][3]
        for i in range(len(pts) - 1):
            x0, l0, m0, s0 = pts[i]
            x1, l1, m1, s1 = pts[i + 1]
            if x0 <= x <= x1:
                t = (x - x0) / (x1 - x0) if x1 > x0 else 0.0
                return (l0 + t * (l1 - l0), m0 + t * (m1 - m0),
                        s0 + t * (s1 - s0))
        raise Error("interpolation failed — anchor table corrupt")

    def lms(self, measure, sex, x):
        if measure not in MEASURES:
            raise Error(f"unknown measure: {measure}")
        if sex not in SEXES:
            raise Error(f"unknown sex: {sex}")
        lo, hi = _DOMAIN[measure]
        if not (lo <= x <= hi):
            raise Error(
                f"{measure} reference covers {lo}–{hi}; {x} is outside — "
                "an answer beyond the data is a lie")
        return self._interpolate(self.tables[(measure, sex)], x)


def classify(measure, z):
    """WHO severity cuts. Refuses the impossible rather than guessing."""
    if not (-8.0 <= z <= 8.0):
        raise Error(f"z={z:.2f} is not a plausible measurement — "
                    "check the scale, not the child")
    if z < -3.0:
        band = "severe"
    elif z < -2.0:
        band = "moderate"
    elif measure == "wfh" and z > 2.0:
        band = "overweight-risk"
    else:
        band = "normal"
    return band


class Anthropometry:
    """The clinic-side grading instrument."""

    def __init__(self, registry=None):
        self.registry = registry or LMSRegistry()

    def z_score(self, measure, sex, *, x, value):
        """x: age in months (wfa/hfa) or height in cm (wfh).
        value: weight kg (wfa/wfh) or height cm (hfa)."""
        if measure not in MEASURES:
            raise Error(f"unknown measure: {measure}")
        if sex not in SEXES:
            raise Error(f"unknown sex: {sex}")
        plo, phi = _PLAUSIBLE[measure]
        if not (plo <= value <= phi):
            raise Error(
                f"{measure} value {value} outside plausible "
                f"{plo}–{phi} — refused, not clamped")
        l, m, s = self.registry.lms(measure, sex, x)
        z = _lms_z(value, l, m, s)
        band = classify(measure, z)
        return {
            "measure": measure, "sex": sex, "x": x, "value": value,
            "z": round(z, 2), "band": band,
            "table_status": self.registry.status,
            "version": VERSION,
        }

    def grade_child(self, sex, *, age_months, weight_kg, height_cm=None):
        """The full picture for one visit: WFA + HFA (+ WFH when height
        is present). Absence is stated, not skipped."""
        out = {"sex": sex, "age_months": age_months,
               "table_status": self.registry.status,
               "version": VERSION}
        out["wfa"] = self.z_score("wfa", sex, x=age_months,
                                  value=weight_kg)
        if height_cm is not None:
            out["hfa"] = self.z_score("hfa", sex, x=age_months,
                                      value=height_cm)
            out["wfh"] = self.z_score("wfh", sex, x=height_cm,
                                      value=weight_kg)
        else:
            out["hfa"] = None  # stated, not skipped
            out["wfh"] = None
        body = json.dumps(out, sort_keys=True).encode()
        out["sha256"] = hashlib.sha256(body).hexdigest()
        return out
