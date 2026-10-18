#!/usr/bin/env python3
"""Quality engine tests: criteria engine, per-subject proportion
measures, MeasureReport shape, cross-facility comparability."""
import json
import os
import unittest

from quality import (evaluate, evaluate_library, get_path, in_period,
                     load_measure_file, matches, scorecard)

MEASURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "fhir", "measures")


def enc(rid, patient, etype, facility, day="2026-03-10", **extra):
    e = {"resourceType": "Encounter", "id": rid,
         "class": {"code": extra.pop("cls", "AMB")},
         "type": [{"coding": [{"code": etype}]}],
         "subject": {"reference": f"Patient/{patient}"},
         "serviceProvider": {"reference": f"Location/{facility}"},
         "period": {"start": day}}
    e.update(extra)
    return e


def immun(rid, patient, vaccine, facility, day="2026-03-10"):
    return {"resourceType": "Immunization", "id": rid, "status": "completed",
            "vaccineCode": {"coding": [{"code": vaccine}]},
            "patient": {"reference": f"Patient/{patient}"},
            "location": {"reference": f"Location/{facility}"},
            "occurrenceDateTime": day}


def appt(rid, status, facility, day="2026-03-10"):
    return {"resourceType": "Appointment", "id": rid, "status": status,
            "serviceProvider": {"reference": f"Location/{facility}"},
            "start": day}


class TestCriteria(unittest.TestCase):
    def test_get_path_codeable_and_scalar(self):
        r = {"type": [{"coding": [{"code": "anc-visit"}]}],
             "valueQuantity": {"value": 120}}
        self.assertEqual(get_path(r, "type.coding"), "anc-visit")
        self.assertEqual(get_path(r, "valueQuantity.value"), 120)
        self.assertIsNone(get_path(r, "missing.path"))

    def test_ops(self):
        r = {"status": "booked", "n": 5}
        self.assertTrue(matches(r, [{"path": "status", "op": "in",
                                     "value": ["booked", "fulfilled"]}]))
        self.assertTrue(matches(r, [{"path": "n", "op": "gte", "value": 4}]))
        self.assertFalse(matches(r, [{"path": "n", "op": "lt", "value": 4}]))

    def test_in_period(self):
        r = {"period": {"start": "2026-03-10"}}
        self.assertTrue(in_period(r, "2026-03-01", "2026-03-31"))
        self.assertFalse(in_period(r, "2026-04-01", "2026-04-30"))


class TestMeasures(unittest.TestCase):
    def setUp(self):
        self.measures = {f[:-5]: load_measure_file(os.path.join(MEASURES_DIR, f))
                         for f in os.listdir(MEASURES_DIR) if f.endswith(".json")}
        self.period = ("2026-03-01", "2026-03-31")

    def test_library_loads(self):
        for want in ("anc-four-visits", "immunization-penta1",
                     "hypertension-control", "appointment-show-rate",
                     "emergency-arrival-confirmed"):
            self.assertIn(want, self.measures)

    def test_anc_per_subject(self):
        bundle = [
            enc("e1", "P1", "anc-registration", "FAC-A"),
            enc("e2", "P1", "anc-visit", "FAC-A", sequence=4),   # counts
            enc("e3", "P2", "anc-registration", "FAC-A"),
            enc("e4", "P2", "anc-visit", "FAC-A", sequence=2),   # too few
            enc("e5", "P3", "anc-registration", "FAC-A", day="2026-04-15"),  # out of period
        ]
        r = evaluate(self.measures["anc-four-visits"], bundle, "FAC-A", *self.period)
        den = next(p for p in r.populations if p.code == "denominator")
        num = next(p for p in r.populations if p.code == "numerator")
        self.assertEqual(den.count, 2)   # P1, P2 — P3 out of period
        self.assertEqual(num.count, 1)   # only P1 reached visit 4
        self.assertAlmostEqual(r.rate, 0.5)

    def test_immunization_cross_facility(self):
        bundles = {
            "FAC-A": [enc("e1", "C1", "child-registration", "FAC-A"),
                      immun("i1", "C1", "penta-1", "FAC-A")],
            "FAC-B": [enc("e2", "C2", "child-registration", "FAC-B"),
                      enc("e3", "C3", "child-registration", "FAC-B"),
                      immun("i2", "C3", "penta-1", "FAC-B"),
                      immun("i3", "C2", "bcg", "FAC-B")],  # wrong vaccine
        }
        results = evaluate_library([self.measures["immunization-penta1"]],
                                   bundles, *self.period)
        grid = scorecard(results)["facilities"]
        self.assertAlmostEqual(grid["FAC-A"]["immunization_penta1"]["rate"], 1.0)
        self.assertAlmostEqual(grid["FAC-B"]["immunization_penta1"]["rate"], 0.5)

    def test_facility_scoping(self):
        # resources of FAC-B must not leak into FAC-A's report
        bundle = [enc("e1", "P1", "anc-registration", "FAC-B"),
                  enc("e2", "P1", "anc-visit", "FAC-B", sequence=4)]
        r = evaluate(self.measures["anc-four-visits"], bundle, "FAC-A", *self.period)
        self.assertIsNone(r.rate)  # denominator 0 -> data-absent, not 0%

    def test_appointment_show_rate(self):
        bundle = [appt("a1", "fulfilled", "FAC-A"),
                  appt("a2", "fulfilled", "FAC-A"),
                  appt("a3", "noshow", "FAC-A"),
                  appt("a4", "cancelled", "FAC-A")]  # cancelled excluded from denom
        r = evaluate(self.measures["appointment-show-rate"], bundle, "FAC-A",
                     *self.period)
        den = next(p for p in r.populations if p.code == "denominator")
        self.assertEqual(den.count, 3)
        self.assertAlmostEqual(r.rate, 2 / 3)

    def test_emergency_arrival_measure(self):
        bundle = [
            enc("x1", "P1", "emergency", "FAC-A", cls="EMER",
                arrivalConfirmedWithinSla=True),
            enc("x2", "P2", "emergency", "FAC-A", cls="EMER",
                arrivalConfirmedWithinSla=False),
        ]
        r = evaluate(self.measures["emergency-arrival-confirmed"], bundle,
                     "FAC-A", *self.period)
        self.assertAlmostEqual(r.rate, 0.5)

    def test_measure_report_shape(self):
        bundle = [appt("a1", "fulfilled", "FAC-A")]
        r = evaluate(self.measures["appointment-show-rate"], bundle, "FAC-A",
                     *self.period)
        rep = r.to_measure_report()
        self.assertEqual(rep["resourceType"], "MeasureReport")
        self.assertEqual(rep["type"], "summary")
        self.assertEqual(rep["subject"]["reference"], "Location/FAC-A")
        self.assertEqual(rep["period"]["start"], "2026-03-01")
        self.assertEqual(rep["group"][0]["measureScore"]["value"], 1.0)
        # json round-trip: the report is a wire-ready FHIR resource
        json.dumps(rep)

    def test_duplicates_counted_once(self):
        bundle = [appt("a1", "fulfilled", "FAC-A"),
                  dict(appt("a1", "fulfilled", "FAC-A"))]  # same id twice
        r = evaluate(self.measures["appointment-show-rate"], bundle, "FAC-A",
                     *self.period)
        den = next(p for p in r.populations if p.code == "denominator")
        self.assertEqual(den.count, 1)


if __name__ == "__main__":
    unittest.main()
