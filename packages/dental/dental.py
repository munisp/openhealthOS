"""dental.py — odontogram and dental charting.

Dentistry charts teeth, not narratives. FDI notation (11–48) is the
vocabulary; every tooth carries a surface-level condition map. A tooth
recorded as extracted cannot later grow a filling — state transitions
are the clinical truth.

Doctrine:
- FDI ONLY: teeth are 11–18, 21–28, 31–38, 41–48 (permanent) and
  51–85 (deciduous). Anything else refuses.
- CONDITIONS ARE AN ENUM: sound / caries / filled / crown / missing /
  extracted / root-canal / fractured / implant / watch.
- EXTRACTION IS TERMINAL: an extracted tooth refuses further surface
  work; a new entry needs a stated reason (e.g. wrong-tooth correction,
  which cites the entry it amends).
- THE ODONTOGRAM IS CHECKSUMMED: the whole mouth at a point in time
  carries a sha256 — a changed chart is a detectable chart.
"""

import hashlib
import json

VERSION = "dental@1.0.0"

PERMANENT = [t for q in (1, 2, 3, 4) for t in
             range(q * 10 + 1, q * 10 + 9)]
DECIDUOUS = [t for q in (5, 6, 7, 8) for t in
             range(q * 10 + 1, q * 10 + 9)]
VALID_TEETH = set(PERMANENT + DECIDUOUS)

CONDITIONS = ("sound", "watch", "caries", "filled", "crown",
              "root-canal", "fractured", "missing", "extracted",
              "implant")
SURFACES = ("mesial", "distal", "occlusal", "buccal", "lingual")

# which conditions may follow which (terminal states refuse further work)
TERMINAL_CONDITIONS = ("extracted", "implant")
TERMINAL_BY_CLASS = ("extracted", "implant")


class Error(Exception):
    pass


class ToothRecord:
    def __init__(self, fdi):
        if fdi not in VALID_TEETH:
            raise Error(f"FDI {fdi} is not a tooth — refused")
        self.fdi = fdi
        self.entries = []  # append-only

    @property
    def current(self):
        return self.entries[-1]["condition"] if self.entries else "sound"

    def record(self, condition, *, by, day, surfaces=None, note=None,
               amends=None):
        if condition not in CONDITIONS:
            raise Error(f"unknown condition: {condition}")
        if not by:
            raise Error("a dental entry names the clinician")
        if self.current in TERMINAL_CONDITIONS and not amends:
            raise Error(
                f"tooth {self.fdi} is {self.current} — terminal; "
                "further entries must cite the entry they correct")
        if surfaces:
            for s in surfaces:
                if s not in SURFACES:
                    raise Error(f"unknown surface: {s}")
        if amends is not None and not (note and note.strip()):
            raise Error("a correction states its reason")
        entry = {"condition": condition, "by": by, "day": day,
                 "surfaces": surfaces or [], "note": note,
                 "amends": amends,
                 "seq": len(self.entries) + 1}
        self.entries.append(entry)
        return entry


class Odontogram:
    """The whole mouth, one patient, one point in time."""

    def __init__(self, patient_ref):
        if not patient_ref:
            raise Error("an odontogram belongs to a patient")
        self.patient_ref = patient_ref
        self.teeth = {}  # fdi -> ToothRecord

    def tooth(self, fdi):
        if fdi not in self.teeth:
            self.teeth[fdi] = ToothRecord(fdi)
        return self.teeth[fdi]

    def snapshot(self, day):
        per_tooth = {}
        for fdi in sorted(self.teeth):
            tr = self.teeth[fdi]
            per_tooth[fdi] = {"condition": tr.current,
                              "entries": len(tr.entries)}
        counts = {}
        for t in per_tooth.values():
            counts[t["condition"]] = counts.get(t["condition"], 0) + 1
        body = {"patient_ref": self.patient_ref, "day": day,
                "teeth": per_tooth, "counts": counts,
                "version": VERSION}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        return body
