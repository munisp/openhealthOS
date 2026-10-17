"""OpenHealth OS — laboratory information system bridge.

Spec diagnostics module: hub-and-spoke lab networking. Spoke facilities
(PHCs) collect samples; a courier chain moves them to a hub lab; results
travel back as FHIR R4 Observations. The bridge tracks the sample chain
of custody and converts raw analyzer results into FHIR, with critical-
value flagging — a critical potassium sitting unnoticed in a hub queue
is a patient-safety failure, so criticals are surfaced, not just stored.

FHIR output is plain dicts (R4, fhirVersion 4.0.1) — the same shape the
repo's StructureDefinitions in fhir/profiles constrain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


# ------------------------------------------------------------ catalogue

@dataclass(frozen=True)
class TestDef:
    code: str          # LOINC code
    name: str
    unit: str          # UCUM unit code
    ref_low: float
    ref_high: float
    critical_low: float | None = None
    critical_high: float | None = None


TEST_CATALOGUE: dict[str, TestDef] = {
    "K": TestDef("6298-4", "Potassium [Moles/volume] in Blood", "mmol/L",
                 3.5, 5.1, critical_low=2.5, critical_high=6.5),
    "GLU": TestDef("2339-0", "Glucose [Mass/volume] in Blood", "mg/dL",
                   70, 140, critical_low=40, critical_high=500),
    "HB": TestDef("718-7", "Hemoglobin [Mass/volume] in Blood", "g/dL",
                  12.0, 16.0, critical_low=7.0, critical_high=20.0),
    "MAL-RDT": TestDef("94558-4", "Plasmodium sp Ag [Presence] in Blood", "{presence}",
                       0, 0),
}


# ------------------------------------------------------------- workflow

class SampleState(str, Enum):
    ORDERED = "ordered"
    COLLECTED = "collected"
    IN_TRANSIT = "in-transit"
    RECEIVED_AT_HUB = "received-at-hub"
    RESULTED = "resulted"
    DELIVERED = "delivered"


# Legal moves along the hub-and-spoke chain. Anything else is a custody
# error: samples do not teleport, and results do not precede receipt.
TRANSITIONS: dict[SampleState, set[SampleState]] = {
    SampleState.ORDERED: {SampleState.COLLECTED},
    SampleState.COLLECTED: {SampleState.IN_TRANSIT},
    SampleState.IN_TRANSIT: {SampleState.RECEIVED_AT_HUB},
    SampleState.RECEIVED_AT_HUB: {SampleState.RESULTED},
    SampleState.RESULTED: {SampleState.DELIVERED},
    SampleState.DELIVERED: set(),
}


class ChainOfCustodyError(ValueError):
    pass


@dataclass
class LabOrder:
    order_id: str
    patient_id: str
    spoke_facility_id: str
    hub_lab_id: str
    test: str  # key into TEST_CATALOGUE
    priority: str = "routine"  # "routine" | "urgent"
    state: SampleState = SampleState.ORDERED
    history: list[tuple[SampleState, datetime]] = field(default_factory=list)
    result_value: float | None = None
    resulted_at: datetime | None = None

    def advance(self, to: SampleState, at: datetime) -> None:
        if to not in TRANSITIONS[self.state]:
            raise ChainOfCustodyError(
                f"illegal transition {self.state.value} → {to.value} for {self.order_id}"
            )
        self.history.append((self.state, at))
        self.state = to

    def record_result(self, value: float, at: datetime) -> None:
        if self.state != SampleState.RECEIVED_AT_HUB:
            raise ChainOfCustodyError(
                f"cannot result {self.order_id} in state {self.state.value} — "
                "sample must be at the hub"
            )
        self.result_value = value
        self.resulted_at = at
        self.advance(SampleState.RESULTED, at)

    def turnaround_hours(self) -> float | None:
        """Order-to-result turnaround, once resulted."""
        if self.resulted_at is None or not self.history:
            return None
        ordered_at = self.history[0][1]
        return (self.resulted_at - ordered_at).total_seconds() / 3600


# --------------------------------------------------------------- results

def interpretation(test: TestDef, value: float) -> str:
    """HL7 v2 interpretation code: N / L / H, or C-prefixed when critical."""
    if test.critical_low is not None and value < test.critical_low:
        return "CL"
    if test.critical_high is not None and value > test.critical_high:
        return "CH"
    if value < test.ref_low:
        return "L"
    if value > test.ref_high:
        return "H"
    return "N"


def to_fhir_observation(order: LabOrder) -> dict:
    """Convert a resulted order into a FHIR R4 Observation (dict form)."""
    if order.state not in (SampleState.RESULTED, SampleState.DELIVERED):
        raise ChainOfCustodyError(
            f"order {order.order_id} has no result to serialize (state={order.state.value})"
        )
    test = TEST_CATALOGUE[order.test]
    interp = interpretation(test, order.result_value)
    return {
        "resourceType": "Observation",
        "id": f"obs-{order.order_id}",
        "status": "final",
        "category": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "laboratory",
            }],
        }],
        "code": {
            "coding": [{
                "system": "http://loinc.org",
                "code": test.code,
                "display": test.name,
            }],
            "text": test.name,
        },
        "subject": {"reference": f"Patient/{order.patient_id}"},
        "effectiveDateTime": order.resulted_at.isoformat(),
        "valueQuantity": {
            "value": order.result_value,
            "unit": test.unit,
            "system": "http://unitsofmeasure.org",
            "code": test.unit,
        },
        "interpretation": [{
            "coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                "code": interp,
            }],
        }],
        "referenceRange": [{
            "low": {"value": test.ref_low, "unit": test.unit},
            "high": {"value": test.ref_high, "unit": test.unit},
        }],
    }


# ---------------------------------------------------------------- queue

@dataclass
class LabQueue:
    """The hub lab's working set: orders, custody, critical surfacing."""

    orders: dict[str, LabOrder] = field(default_factory=dict)

    def place(self, order: LabOrder) -> LabOrder:
        if order.test not in TEST_CATALOGUE:
            raise ValueError(f"unknown test: {order.test!r}")
        self.orders[order.order_id] = order
        return order

    def result(self, order_id: str, value: float, at: datetime,
               shr_url: str | None = None, facility_id: str | None = None) -> dict:
        """Record a result and return the FHIR Observation.

        When shr_url is given, the observation is pushed into the shared
        health record immediately. Push failures never fail the result:
        the envelope reports shr_pushed=False and the observation stays
        available for the next sync pass.
        """
        order = self.orders[order_id]
        order.record_result(value, at)
        obs = to_fhir_observation(order)
        if shr_url:
            from shr import push_observation  # local module, stdlib-only
            try:
                push_observation(shr_url, obs, patient_id=order.patient_id,
                                 facility_id=facility_id or order.spoke_facility_id)
                obs["shr_pushed"] = True
            except (ConnectionError, ValueError, OSError) as e:
                obs["shr_pushed"] = False
                obs["shr_error"] = str(e)
        return obs

    def critical_results(self) -> list[LabOrder]:
        """Resulted-but-undelivered orders with critical values.

        This is the patient-safety view: everything on this list needs a
        phone call to the spoke facility now, not at the next sync.
        """
        criticals = []
        for order in self.orders.values():
            if order.state != SampleState.RESULTED or order.result_value is None:
                continue
            test = TEST_CATALOGUE[order.test]
            if interpretation(test, order.result_value) in ("CL", "CH"):
                criticals.append(order)
        return criticals
