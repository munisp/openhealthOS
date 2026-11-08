"""emar.py — the electronic medication administration record (eMAR).

CPOE writes the order; pharmacy dispenses it; the eMAR is where the third
leg of the stool stands: a named nurse gave a named patient a named drug
at a recorded minute. Without this record the medication loop is open —
nobody can prove the dose happened, and nobody can prove it did not.

Doctrine:
- NO DISPENSE, NO MAR: only orders in 'dispensed' state may generate dose
  slots. An undispensed order is a wish, not a task.
- SCAN OR EXPLAIN: administration requires the scanned patient barcode and
  the scanned medication barcode to match the slot's expectations. A
  mismatch refuses, naming which barcode failed. Where a ward genuinely
  has no scanner, an explicit barcode_exception reason is recorded —
  the exception is data, not silence.
- HIGH-ALERT MEANS TWO PEOPLE: drugs on the high-alert registry require a
  named witness at administration (insulin, anticoagulants, IV potassium,
  opioids, oxytocin).
- OUTSIDE THE WINDOW NEEDS A REASON: doses have a grace window; giving
  early or late without a reason refuses.
- REFUSAL IS A RESULT: refused/held/omitted doses are terminal states with
  a mandatory reason — the MAR must show the dose was NOT given and why.
- OVERDUE IS VISIBLE: mar_for() surfaces scheduled doses with no event.
"""

import hashlib
import json

VERSION = "emar@1.0.0"

# frequency -> administration minutes of day
FREQUENCIES = {
    "od": (8 * 60,),
    "mane": (8 * 60,),
    "nocte": (22 * 60,),
    "bd": (8 * 60, 20 * 60),
    "tds": (8 * 60, 14 * 60, 20 * 60),
    "qid": (8 * 60, 12 * 60, 18 * 60, 22 * 60),
    "stat": (),
    "prn": (),
}

GRACE_MINUTES = 60
TERMINAL = ("given", "refused", "held", "omitted")

# High-alert registry: administration requires a named witness.
# Entries, not branches — deployers extend via register_high_alert().
HIGH_ALERT = {
    "insulins", "anticoagulants", "iv-potassium", "opioids", "oxytocin",
    "chemotherapy", "iv-sedatives",
}


class Error(Exception):
    pass


def register_high_alert(drug_class):
    if not drug_class or not drug_class.strip():
        raise Error("high-alert entries name a drug class")
    HIGH_ALERT.add(drug_class.strip())


class DoseSlot:
    """One scheduled administration of one order."""

    def __init__(self, slot_id, order, scheduled_minute, day):
        self.slot_id = slot_id
        self.order_id = order["order_id"]
        self.patient_ref = order["patient_ref"]
        self.drug_code = order["drug_code"]
        self.drug_class = order.get("drug_class", "")
        self.dose = order["dose"]
        self.route = order.get("route", "oral")
        self.patient_barcode = order.get("patient_barcode", "")
        self.medication_barcode = order.get("medication_barcode", "")
        self.scheduled_minute = scheduled_minute
        self.day = day
        self.prn = order.get("frequency") == "prn"
        self.state = "scheduled"
        self.event = None  # {state, by, minute, reason, witness, exception}

    def high_alert(self):
        return self.drug_class in HIGH_ALERT

    def _check_barcodes(self, patient_barcode, medication_barcode,
                        barcode_exception):
        if barcode_exception:
            if not barcode_exception.strip():
                raise Error("a barcode exception states its reason")
            return barcode_exception.strip()
        if not self.patient_barcode or not self.medication_barcode:
            raise Error(
                "slot carries no barcodes — scan impossible; "
                "record a barcode_exception reason")
        if patient_barcode != self.patient_barcode:
            raise Error("patient barcode mismatch — wrong wristband")
        if medication_barcode != self.medication_barcode:
            raise Error("medication barcode mismatch — wrong drug or strength")
        return None

    def _check_window(self, minute, reason):
        if self.prn or self.scheduled_minute is None:
            return  # prn/stat doses are event-driven
        delta = minute - self.scheduled_minute
        if abs(delta) > GRACE_MINUTES and not (reason and reason.strip()):
            raise Error(
                f"dose is {abs(delta)}min outside the window — "
                "state a reason")

    def record(self, state, *, by, minute, reason=None, witness=None,
               patient_barcode=None, medication_barcode=None,
               barcode_exception=None):
        if self.state in TERMINAL:
            raise Error(f"dose already {self.state} — record a new event, "
                        "never overwrite the MAR")
        if state not in TERMINAL:
            raise Error(f"unknown administration state: {state}")
        if not by:
            raise Error("administration names the nurse")
        if state != "given":
            if not (reason and reason.strip()):
                raise Error(f"a {state} dose states its reason")
        else:
            self._check_window(minute, reason)
        witness_used = None
        if self.high_alert():
            if state == "given":
                if not witness or witness == by:
                    raise Error(
                        f"{self.drug_class} is high-alert — a second named "
                        "person witnesses administration")
                witness_used = witness
        exception_used = None
        if state == "given":
            exception_used = self._check_barcodes(
                patient_barcode, medication_barcode, barcode_exception)
        self.state = state
        self.event = {
            "state": state, "by": by, "minute": minute,
            "reason": reason, "witness": witness_used,
            "barcode_exception": exception_used,
            "day": self.day,
        }
        return self.event


class MAR:
    """The medication administration record for one admission."""

    def __init__(self, mar_id, patient_ref, admission_ref, patient_barcode):
        if not patient_ref or not admission_ref:
            raise Error("a MAR belongs to a patient in an admission")
        self.mar_id = mar_id
        self.patient_ref = patient_ref
        self.admission_ref = admission_ref
        self.patient_barcode = patient_barcode
        self.orders = {}   # order_id -> order dict
        self.slots = {}    # slot_id -> DoseSlot
        self._n = 0

    def add_order(self, order, *, day):
        """Place a dispensed order on the MAR and schedule its doses."""
        if order.get("state") != "dispensed":
            raise Error("only dispensed orders reach the MAR — "
                        "an undispensed order is a wish, not a task")
        if order.get("patient_ref") != self.patient_ref:
            raise Error("order belongs to a different patient")
        freq = order.get("frequency")
        if freq not in FREQUENCIES:
            raise Error(f"unknown frequency: {freq}")
        order = dict(order)
        order.setdefault("patient_barcode", self.patient_barcode)
        order["started_day"] = day
        self.orders[order["order_id"]] = order
        times = FREQUENCIES[freq]
        duration = order.get("duration_days", 1)
        made = []
        for d in range(day, day + max(1, duration)):
            for t in times:
                self._n += 1
                slot = DoseSlot(f"{self.mar_id}-S{self._n}", order, t, d)
                self.slots[slot.slot_id] = slot
                made.append(slot.slot_id)
        return made

    def discontinue_order(self, order_id, *, by, day, reason):
        if order_id not in self.orders:
            raise Error("unknown order")
        if not (reason and reason.strip()):
            raise Error("discontinuation states its reason")
        dropped = 0
        for s in self.slots.values():
            if s.order_id == order_id and s.state == "scheduled" \
                    and s.day >= day:
                s.state = "omitted"
                s.event = {"state": "omitted", "by": by, "minute": None,
                           "reason": f"discontinued: {reason}",
                           "witness": None, "barcode_exception": None,
                           "day": s.day}
                dropped += 1
        return dropped

    def give_prn(self, order_id, *, by, minute, day, indication,
                 patient_barcode=None, medication_barcode=None,
                 barcode_exception=None, witness=None):
        order = self.orders.get(order_id)
        if not order or order.get("frequency") != "prn":
            raise Error("PRN administration needs a PRN order on this MAR")
        if not (indication and indication.strip()):
            raise Error("a PRN dose states its indication")
        self._n += 1
        slot = DoseSlot(f"{self.mar_id}-S{self._n}", order, None, day)
        self.slots[slot.slot_id] = slot
        event = slot.record(
            "given", by=by, minute=minute, reason=indication,
            witness=witness, patient_barcode=patient_barcode,
            medication_barcode=medication_barcode,
            barcode_exception=barcode_exception)
        return slot.slot_id, event

    def sheet(self, day):
        """The day's MAR: every slot with its state; overdue surfaced."""
        rows = []
        for s in sorted(self.slots.values(),
                        key=lambda x: (x.day, x.scheduled_minute or 0,
                                       x.slot_id)):
            if s.day != day:
                continue
            rows.append({
                "slot_id": s.slot_id, "order_id": s.order_id,
                "drug_code": s.drug_code, "dose": s.dose,
                "route": s.route,
                "scheduled_minute": s.scheduled_minute,
                "state": s.state, "high_alert": s.high_alert(),
                "event": s.event,
                "overdue": s.state == "scheduled",
            })
        return {"mar_id": self.mar_id, "patient_ref": self.patient_ref,
                "day": day, "rows": rows, "version": VERSION,
                "sha256": self.fingerprint(day)}

    def fingerprint(self, day):
        body = {"mar_id": self.mar_id, "day": day,
                "rows": [(s.slot_id, s.state,
                          s.event["minute"] if s.event else None)
                         for s in self.slots.values() if s.day == day]}
        return hashlib.sha256(json.dumps(body, sort_keys=True)
                              .encode()).hexdigest()

    def audit(self, day):
        """Completion audit: given vs refused vs held vs overdue."""
        rows = self.sheet(day)["rows"]
        out = {"given": 0, "refused": 0, "held": 0, "omitted": 0,
               "scheduled": 0, "high_alert_witnessed": 0,
               "barcode_exceptions": 0}
        for r in rows:
            out[r["state"] if r["state"] in out else "scheduled"] += 1
            ev = r["event"]
            if ev and ev.get("witness"):
                out["high_alert_witnessed"] += 1
            if ev and ev.get("barcode_exception"):
                out["barcode_exceptions"] += 1
        out["total"] = len(rows)
        out["version"] = VERSION
        return out
