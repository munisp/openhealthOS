#!/usr/bin/env python3
"""DHIS2 exporter tests: mapping honesty, period shaping, suppressed
cells never export, spool replay order."""
import json
import os
import tempfile
import unittest

from dhis2 import (Mapping, data_value_set, export_batch,
                   measure_report_to_values, period_of,
                   surveillance_to_values)

MAP = Mapping(
    org_units={"FAC-A": "OU123456789", "KANO-NMC": "OU987654321"},
    data_elements={
        "appointment_show_rate_denominator": "DE_SHOW_DEN",
        "appointment_show_rate_numerator": "DE_SHOW_NUM",
        "appointment_show_rate_rate": "DE_SHOW_RATE",
        "surveillance_measles": "DE_MEASLES",
    })


def report():
    return {
        "resourceType": "MeasureReport", "status": "complete", "type": "summary",
        "measure": "https://openhealthos.org/fhir/Measure/appointment-show-rate",
        "period": {"start": "2026-03-01", "end": "2026-03-31"},
        "subject": {"reference": "Location/FAC-A"},
        "group": [{
            "population": [
                {"code": {"coding": [{"code": "denominator"}]}, "count": 30},
                {"code": {"coding": [{"code": "numerator"}]}, "count": 21},
                {"code": {"coding": [{"code": "excluded"}]}, "count": 4},
            ],
            "measureScore": {"value": 0.7},
        }],
    }


class TestMeasureReportExport(unittest.TestCase):
    def test_values_and_period(self):
        values, warnings = measure_report_to_values(report(), MAP)
        self.assertEqual(period_of(report()), "202603")
        by_de = {v["dataElement"]: v["value"] for v in values}
        self.assertEqual(by_de["DE_SHOW_DEN"], 30)
        self.assertEqual(by_de["DE_SHOW_NUM"], 21)
        self.assertEqual(by_de["DE_SHOW_RATE"], 0.7)
        # unmapped population is warned, not dropped silently
        self.assertTrue(any("excluded" in w for w in warnings))
        for v in values:
            self.assertEqual(v["orgUnit"], "OU123456789")
            self.assertEqual(v["period"], "202603")

    def test_unmapped_facility_reports_not_drops(self):
        values, warnings = measure_report_to_values(report(),
                                                    Mapping(data_elements=MAP.data_elements))
        self.assertEqual(values, [])
        self.assertTrue(any("FAC-A" in w for w in warnings))


class TestSurveillanceExport(unittest.TestCase):
    def test_suppressed_never_exports(self):
        rows = [
            {"disease": "measles", "area": "KANO-NMC", "day": "2026-03-14",
             "count": 7, "suppressed": False},
            {"disease": "cholera", "area": "KANO-NMC", "day": "2026-03-14",
             "count": None, "suppressed": True},
        ]
        values, warnings = surveillance_to_values(rows, MAP)
        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["dataElement"], "DE_MEASLES")
        self.assertEqual(values[0]["value"], 7)


class TestSpool(unittest.TestCase):
    def test_spool_appends_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            spool = os.path.join(d, "spool", "dhis2.jsonl")
            export_batch([{"dataElement": "a", "value": 1}], spool)
            export_batch([{"dataElement": "b", "value": 2}], spool)
            lines = open(spool).read().strip().split("\n")
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["dataValues"][0]["dataElement"], "a")

    def test_payload_shape(self):
        p = data_value_set([{"dataElement": "x", "period": "202603",
                             "orgUnit": "ou", "value": 5}])
        self.assertEqual(list(p.keys()), ["dataValues"])
        json.dumps(p)


if __name__ == "__main__":
    unittest.main()
