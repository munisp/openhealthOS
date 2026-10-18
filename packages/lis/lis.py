"""Laboratory worklist: order -> sample -> result -> verified.

Stdlib-only LIS core. The invariants that matter in a low-resource lab:
- no result without a collected sample (barcode fantasy results are a real
  fraud and error vector),
- no report release without verification by a *different* cadre than the
  one who ran the analyser (four-eyes principle),
- critical results are flagged and must be acknowledged by a clinician,
  with the acknowledgement time recorded — that delay is a quality measure.
- results feed packages/surveillance (confirmatory labs flip suspected ->
  confirmed, per ADR-0020).
"""

import time

ORDER_STATES = ("ordered", "collected", "in_analysis", "resulted",
                "verified", "cancelled")

# Critical limits: value outside these is a critical result.
CRITICAL_LIMITS = {
    "glucose_mmol": (2.2, 25.0),
    "potassium_mmol": (2.5, 6.5),
    "haemoglobin_gdl": (5.0, 20.0),
    "platelets_x109": (20.0, 1000.0),
}


class Error(Exception):
    pass


class LabOrder:
    def __init__(self, order_id, patient_id, test, ordered_by, priority="routine"):
        if priority not in {"routine", "urgent", "stat"}:
            raise Error(f"unknown priority: {priority}")
        if not ordered_by:
            raise Error("orders name the ordering clinician")
        self.id = order_id
        self.patient_id = patient_id
        self.test = test
        self.ordered_by = ordered_by
        self.priority = priority
        self.state = "ordered"
        self.sample_id = None
        self.result = None
        self.critical = False
        self.acknowledged_by = None
        self.acknowledged_at = None
        self.resulted_at = None


class Lab:
    def __init__(self, facility_id):
        self.facility_id = facility_id
        self.orders = {}
        self._seq = 0
        self._sample_seq = 0

    def order(self, patient_id, test, ordered_by, priority="routine"):
        self._seq += 1
        o = LabOrder(f"LAB-{self._seq:06d}", patient_id, test, ordered_by, priority)
        self.orders[o.id] = o
        return o

    def collect(self, order_id, collected_by):
        o = self._at(order_id, "ordered")
        if not collected_by:
            raise Error("samples name who collected them")
        self._sample_seq += 1
        o.sample_id = f"SMP-{self._sample_seq:06d}"
        o.state = "collected"
        return o

    def start_analysis(self, order_id):
        o = self._at(order_id, "collected")
        o.state = "in_analysis"
        return o

    def result(self, order_id, value, unit, entered_by, ref_low=None, ref_high=None):
        """value may be numeric or text (e.g. 'positive' for an RDT)."""
        o = self._at(order_id, "in_analysis")
        if not entered_by:
            raise Error("results name who entered them")
        o.result = {"value": value, "unit": unit, "entered_by": entered_by,
                    "ref_low": ref_low, "ref_high": ref_high}
        o.resulted_at = time.time()
        if isinstance(value, (int, float)) and o.test in CRITICAL_LIMITS:
            lo, hi = CRITICAL_LIMITS[o.test]
            o.critical = value < lo or value > hi
        o.state = "resulted"
        return o

    def verify(self, order_id, verified_by):
        o = self._at(order_id, "resulted")
        if not verified_by:
            raise Error("verification names the verifier")
        if verified_by == o.result["entered_by"]:
            raise Error("four-eyes: the verifier cannot be the person who entered the result")
        o.state = "verified"
        return o

    def acknowledge_critical(self, order_id, clinician):
        o = self._case(order_id)
        if not o.critical:
            raise Error(f"{order_id} is not a critical result")
        if o.state != "verified":
            raise Error("acknowledge after verification")
        if o.acknowledged_by:
            raise Error("already acknowledged")
        o.acknowledged_by = clinician
        o.acknowledged_at = time.time()
        return o

    def cancel(self, order_id, actor, reason):
        o = self._case(order_id)
        if o.state in ("verified",):
            raise Error("cannot cancel a verified result")
        if not reason:
            raise Error("cancellations need a reason")
        o.state = "cancelled"
        o.result = {"cancelled_by": actor, "reason": reason}
        return o

    def worklist(self, priority_first=True):
        """Open orders, STAT first — the bench's morning."""
        open_orders = [o for o in self.orders.values()
                       if o.state not in ("verified", "cancelled")]
        rank = {"stat": 0, "urgent": 1, "routine": 2}
        open_orders.sort(key=lambda o: (rank[o.priority] if priority_first else 0, o.id))
        return [{"order": o.id, "patient": o.patient_id, "test": o.test,
                 "priority": o.priority, "state": o.state} for o in open_orders]

    def unacknowledged_criticals(self):
        return [o.id for o in self.orders.values()
                if o.critical and not o.acknowledged_by and o.state == "verified"]

    def critical_ack_latency(self):
        """Minutes between result and clinician acknowledgement."""
        lats = [(o.acknowledged_at - o.resulted_at) / 60.0
                for o in self.orders.values()
                if o.critical and o.acknowledged_at and o.resulted_at]
        return round(sum(lats) / len(lats), 2) if lats else None

    def _case(self, order_id):
        if order_id not in self.orders:
            raise Error(f"unknown order: {order_id}")
        return self.orders[order_id]

    def _at(self, order_id, state):
        o = self._case(order_id)
        if o.state != state:
            raise Error(f"order {order_id} is {o.state}, expected {state}")
        return o
