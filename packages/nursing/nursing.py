"""nursing.py — flowsheets, intake/output, and shift handoff.

The ward runs on nursing documentation: the vitals flowsheet, the fluid
balance chart, and the handoff between shifts. Open EMRs lose hospitals
here — admission clerks and doctors are covered, but the nurse's row of
the chart is where deterioration is first visible.

Doctrine:
- PLAUSIBILITY FAIL-CLOSED: a flowsheet row outside survival bounds is a
  typo or a fault; it refuses, naming the bound. Bounds are the same
  per-vital registry used by the device ingest (vitals.VITALS) — one
  home, no drift.
- FLUID BALANCE IS ARITHMETIC, NOT PROSE: intake and output are recorded
  per event; the chart computes running and 24h totals. A negative
  running balance is flagged, never clamped to zero.
- HANDOFF IS TWO SIGNATURES: an SBAR handoff is proposed by the outgoing
  nurse and countersigned by the incoming one. An uncountersigned handoff
  at shift end surfaces in the ward report — silence is not handoff.
- NOTHING OVERWRITES: corrections are new rows that cite the row they
  amend; the original stands.
"""

import hashlib
import json
import os
import sys

# one home for vital bounds — same registry the device ingest uses
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "vitals"))
from vitals import VITALS  # noqa: E402

VERSION = "nursing@1.0.0"

SHIFTS = ("morning", "evening", "night")
IO_KINDS = {
    "intake": ("oral", "iv", "enteral", "blood-product", "other"),
    "output": ("urine", "drain", "vomit", "stool", "ng-aspirate", "other"),
}
IO_PLAUSIBLE_MAX_ML = 5000  # a single event larger than this is a typo


class Error(Exception):
    pass


class Flowsheet:
    """Timestamped vitals grid for one admitted patient."""

    def __init__(self, patient_ref, admission_ref):
        self.patient_ref = patient_ref
        self.admission_ref = admission_ref
        self.rows = []  # dicts; append-only
        self._n = 0

    def record(self, vital, value, *, day, minute, by, amends=None):
        if vital not in VITALS:
            raise Error(f"unknown vital: {vital}")
        _loinc, unit, low, high = VITALS[vital]
        if not (low <= value <= high):
            raise Error(
                f"{vital} {value}{unit} outside plausible "
                f"{low}–{high} — refused, not clamped")
        if not by:
            raise Error("a flowsheet row names its recorder")
        if amends is not None and not any(r["row_id"] == amends
                                          for r in self.rows):
            raise Error("a correction cites the row it amends")
        self._n += 1
        row = {"row_id": f"{self.patient_ref}-FS{self._n}",
               "vital": vital, "value": value, "unit": unit,
               "day": day, "minute": minute, "by": by, "amends": amends}
        self.rows.append(row)
        return row

    def latest(self, vital):
        return max((r for r in self.rows if r["vital"] == vital),
                   key=lambda r: (r["day"], r["minute"]), default=None)

    def series(self, vital, day=None):
        return [r for r in self.rows if r["vital"] == vital
                and (day is None or r["day"] == day)]

    def overdue(self, vital, *, day, minute, max_gap_minutes):
        """Minutes since the last reading of this vital; None if never."""
        series = self.series(vital)
        if not series:
            return None
        last = max(series, key=lambda r: (r["day"], r["minute"]))
        elapsed = (day - last["day"]) * 1440 + (minute - last["minute"])
        return elapsed if elapsed > max_gap_minutes else 0


class FluidBalance:
    """Intake/output chart with running and 24h totals."""

    def __init__(self, patient_ref, admission_ref):
        self.patient_ref = patient_ref
        self.admission_ref = admission_ref
        self.events = []

    def record(self, direction, kind, ml, *, day, minute, by):
        if direction not in IO_KINDS:
            raise Error(f"unknown direction: {direction}")
        if kind not in IO_KINDS[direction]:
            raise Error(f"{kind} is not a {direction} kind")
        if not (0 < ml <= IO_PLAUSIBLE_MAX_ML):
            raise Error(
                f"{ml}ml implausible for one event "
                f"(max {IO_PLAUSIBLE_MAX_ML}) — refused")
        if not by:
            raise Error("a fluid event names its recorder")
        ev = {"direction": direction, "kind": kind, "ml": ml,
              "day": day, "minute": minute, "by": by}
        self.events.append(ev)
        return ev

    def totals(self, day):
        intake = sum(e["ml"] for e in self.events
                     if e["day"] == day and e["direction"] == "intake")
        output = sum(e["ml"] for e in self.events
                     if e["day"] == day and e["direction"] == "output")
        balance = intake - output
        return {"day": day, "intake_ml": intake, "output_ml": output,
                "balance_ml": balance,
                "negative_balance": balance < 0}  # flagged, never clamped

    def running_balance(self, *, day, minute):
        upto = [e for e in self.events
                if (e["day"], e["minute"]) <= (day, minute)]
        return sum(e["ml"] if e["direction"] == "intake" else -e["ml"]
                   for e in upto)


class Handoff:
    """SBAR handoff between shifts — two signatures or it is not done."""

    def __init__(self, handoff_id, patient_ref, *, day, shift,
                 from_nurse, situation, background, assessment,
                 recommendation):
        if shift not in SHIFTS:
            raise Error(f"unknown shift: {shift}")
        for name, val in (("situation", situation), ("background", background),
                          ("assessment", assessment),
                          ("recommendation", recommendation)):
            if not (val and str(val).strip()):
                raise Error(f"SBAR {name} cannot be empty")
        if not from_nurse:
            raise Error("the outgoing nurse names herself")
        self.handoff_id = handoff_id
        self.patient_ref = patient_ref
        self.day = day
        self.shift = shift
        self.from_nurse = from_nurse
        self.sbar = {"situation": situation, "background": background,
                     "assessment": assessment,
                     "recommendation": recommendation}
        self.to_nurse = None
        self.countersigned = False

    def countersign(self, *, by):
        if self.countersigned:
            raise Error("handoff already countersigned")
        if by == self.from_nurse:
            raise Error("the incoming nurse is a different pair of eyes")
        if not by:
            raise Error("the incoming nurse names herself")
        self.to_nurse = by
        self.countersigned = True

    def as_dict(self):
        return {"handoff_id": self.handoff_id,
                "patient_ref": self.patient_ref, "day": self.day,
                "shift": self.shift, "from": self.from_nurse,
                "to": self.to_nurse, "countersigned": self.countersigned,
                "sbar": self.sbar, "version": VERSION}


class WardReport:
    """The ward's day: whose vitals are overdue, whose balance is out,
    which handoffs are unsigned. Attention goes where it is needed."""

    def __init__(self, ward_id):
        self.ward_id = ward_id
        self.flowsheets = {}
        self.balances = {}
        self.handoffs = []

    def add_patient(self, flowsheet, balance=None):
        self.flowsheets[flowsheet.patient_ref] = flowsheet
        if balance is not None:
            self.balances[balance.patient_ref] = balance

    def add_handoff(self, handoff):
        self.handoffs.append(handoff)

    def report(self, *, day, minute, vitals_gap_minutes=360,
               balance_flag_ml=2000):
        overdue = []
        for ref, fs in self.flowsheets.items():
            gaps = {}
            for vital in VITALS:
                g = fs.overdue(vital, day=day, minute=minute,
                               max_gap_minutes=vitals_gap_minutes)
                if g is None or g > 0:
                    gaps[vital] = "never-recorded" if g is None else g
            if gaps:
                overdue.append({"patient_ref": ref, "gaps": gaps})
        fluid_flags = []
        for ref, fb in self.balances.items():
            t = fb.totals(day)
            if abs(t["balance_ml"]) > balance_flag_ml:
                fluid_flags.append({"patient_ref": ref, **t})
        unsigned = [h.as_dict() for h in self.handoffs
                    if h.day == day and not h.countersigned]
        body = {"ward_id": self.ward_id, "day": day,
                "overdue_vitals": overdue, "fluid_flags": fluid_flags,
                "unsigned_handoffs": unsigned}
        body["sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()).hexdigest()
        body["version"] = VERSION
        return body
