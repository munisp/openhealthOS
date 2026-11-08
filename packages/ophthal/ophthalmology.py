"""ophthalmology.py — refraction, visual acuity, and anterior-segment exam.

Beyond the DR screening camera (packages/drscreen), the eye clinic needs
its own record: visual acuity per eye, refraction, IOP, and a slit-lamp
finding set. Glasses are the commonest medical device in the world and
the least documented.

Doctrine:
- LATERALITY IS EXPLICIT: every finding is OD (right), OS (left), or OU
  (both). "The eye" is never an answer.
- ACUITY IS A DENOMINATED FRACTION: stored as numerator/denominator
  (6/6, 6/60), never a float alone — 0.1 means nothing without its
  notation. logMAR conversions are computed, not stored.
- IOP HAS PLAUSIBLE BOUNDS: 0–80 mmHg. Above or below refuses.
- GLASSES PRESCRIPTIONS ARE SIGNED: sphere/cylinder/axis with plausible
  bounds; axis 0–180 only.
"""

import hashlib
import json

VERSION = "ophthalmology@1.0.0"

LATERALITIES = ("OD", "OS", "OU")
ACUITY_NOTATIONS = ("snellen-m", "snellen-ft", "logmar")
IOP_PLAUSIBLE = (0.0, 80.0)
SPHERE_PLAUSIBLE = (-30.0, 30.0)
CYLINDER_PLAUSIBLE = (-10.0, 10.0)
AXIS_PLAUSIBLE = (0, 180)

# common anterior-segment findings (entries, not branches)
FINDINGS = ("clear", "cataract", "pterygium", "corneal-scar",
            "conjunctivitis", "glaucoma-suspect", "uveitis",
            "trachoma", "other")


class Error(Exception):
    pass


class EyeExam:
    """One consultation, both eyes."""

    def __init__(self, exam_id, patient_ref, *, by, day):
        for name, val in (("exam", exam_id), ("patient", patient_ref),
                          ("examiner", by)):
            if not val:
                raise Error(f"an eye exam names its {name}")
        self.exam_id = exam_id
        self.patient_ref = patient_ref
        self.by = by
        self.day = day
        self.acuity = {}      # laterality -> {notation, num, den}
        self.iop = {}         # laterality -> mmHg
        self.refraction = {}  # laterality -> {sphere, cylinder, axis}
        self.findings = {}    # laterality -> [finding]
        self.diagnoses = []

    def record_acuity(self, laterality, notation, numerator, denominator):
        if laterality not in LATERALITIES:
            raise Error(f"unknown laterality: {laterality}")
        if notation not in ACUITY_NOTATIONS:
            raise Error(f"unknown notation: {notation}")
        if denominator <= 0 or numerator <= 0:
            raise Error("acuity is a positive fraction")
        self.acuity[laterality] = {"notation": notation,
                                   "numerator": numerator,
                                   "denominator": denominator}

    def record_iop(self, laterality, mmhg):
        if laterality not in LATERALITIES:
            raise Error(f"unknown laterality: {laterality}")
        if not (IOP_PLAUSIBLE[0] <= mmhg <= IOP_PLAUSIBLE[1]):
            raise Error(f"IOP {mmhg} outside plausible "
                        f"{IOP_PLAUSIBLE[0]}–{IOP_PLAUSIBLE[1]}")
        self.iop[laterality] = mmhg

    def record_refraction(self, laterality, *, sphere, cylinder, axis):
        if laterality not in LATERALITIES:
            raise Error(f"unknown laterality: {laterality}")
        if not (SPHERE_PLAUSIBLE[0] <= sphere <= SPHERE_PLAUSIBLE[1]):
            raise Error("sphere outside plausible bounds")
        if not (CYLINDER_PLAUSIBLE[0] <= cylinder <= CYLINDER_PLAUSIBLE[1]):
            raise Error("cylinder outside plausible bounds")
        if not (AXIS_PLAUSIBLE[0] <= axis <= AXIS_PLAUSIBLE[1]):
            raise Error("axis must be 0–180")
        self.refraction[laterality] = {
            "sphere": sphere, "cylinder": cylinder, "axis": axis,
            "signed": True, "by": self.by}

    def record_finding(self, laterality, finding):
        if laterality not in LATERALITIES:
            raise Error(f"unknown laterality: {laterality}")
        if finding not in FINDINGS:
            raise Error(f"unknown finding: {finding} — register it first")
        self.findings.setdefault(laterality, []).append(finding)

    def as_dict(self):
        body = {"exam_id": self.exam_id, "patient_ref": self.patient_ref,
                "by": self.by, "day": self.day, "acuity": self.acuity,
                "iop": self.iop, "refraction": self.refraction,
                "findings": self.findings, "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body


def snellen_to_logmar(numerator, denominator):
    """Computed, never stored — one home for the math."""
    import math
    if denominator <= 0 or numerator <= 0:
        raise Error("acuity is a positive fraction")
    return round(__import__("math").log10(denominator / numerator), 2)
