"""Tests for the LIS bridge — custody chain, FHIR output, criticals."""

import unittest
from datetime import datetime, timedelta

from lis_bridge import (
    ChainOfCustodyError,
    LabOrder,
    LabQueue,
    SampleState,
    TEST_CATALOGUE,
    interpretation,
    to_fhir_observation,
)

T0 = datetime(2026, 10, 18, 8, 0, 0)


def make_order(test="K"):
    return LabOrder(
        order_id="LAB-001", patient_id="OH-NG-KD-0014-000123",
        spoke_facility_id="NG-KD-0014", hub_lab_id="HUB-KD-01", test=test,
    )


def walk_to_hub(order):
    """Drive an order through the legal chain to the hub."""
    order.advance(SampleState.COLLECTED, T0 + timedelta(hours=1))
    order.advance(SampleState.IN_TRANSIT, T0 + timedelta(hours=2))
    order.advance(SampleState.RECEIVED_AT_HUB, T0 + timedelta(hours=5))
    return order


class TestCustodyChain(unittest.TestCase):
    def test_happy_path_chain(self):
        order = walk_to_hub(make_order())
        self.assertEqual(order.state, SampleState.RECEIVED_AT_HUB)
        self.assertEqual(len(order.history), 3)

    def test_teleport_rejected(self):
        order = make_order()
        with self.assertRaises(ChainOfCustodyError):
            order.advance(SampleState.RESULTED, T0)

    def test_result_before_receipt_rejected(self):
        order = make_order()
        order.advance(SampleState.COLLECTED, T0)
        with self.assertRaises(ChainOfCustodyError):
            order.record_result(4.1, T0 + timedelta(hours=1))

    def test_terminal_state(self):
        order = walk_to_hub(make_order())
        order.record_result(4.1, T0 + timedelta(hours=6))
        order.advance(SampleState.DELIVERED, T0 + timedelta(hours=9))
        with self.assertRaises(ChainOfCustodyError):
            order.advance(SampleState.COLLECTED, T0 + timedelta(hours=10))


class TestFHIRConversion(unittest.TestCase):
    def test_observation_shape(self):
        order = walk_to_hub(make_order())
        order.record_result(4.1, T0 + timedelta(hours=6))
        obs = to_fhir_observation(order)
        self.assertEqual(obs["resourceType"], "Observation")
        self.assertEqual(obs["status"], "final")
        self.assertEqual(obs["code"]["coding"][0]["system"], "http://loinc.org")
        self.assertEqual(obs["code"]["coding"][0]["code"], "6298-4")
        self.assertEqual(obs["valueQuantity"]["system"], "http://unitsofmeasure.org")
        self.assertEqual(obs["valueQuantity"]["value"], 4.1)
        self.assertEqual(obs["interpretation"][0]["coding"][0]["code"], "N")
        self.assertEqual(obs["subject"]["reference"], "Patient/OH-NG-KD-0014-000123")

    def test_unresulted_order_not_serializable(self):
        order = make_order()
        with self.assertRaises(ChainOfCustodyError):
            to_fhir_observation(order)

    def test_interpretation_bands(self):
        k = TEST_CATALOGUE["K"]
        self.assertEqual(interpretation(k, 4.1), "N")
        self.assertEqual(interpretation(k, 3.0), "L")
        self.assertEqual(interpretation(k, 5.5), "H")
        self.assertEqual(interpretation(k, 2.0), "CL")
        self.assertEqual(interpretation(k, 7.0), "CH")


class TestQueue(unittest.TestCase):
    def test_unknown_test_rejected(self):
        queue = LabQueue()
        with self.assertRaises(ValueError):
            queue.place(make_order(test="ZZZ"))

    def test_critical_results_surfaced_until_delivered(self):
        queue = LabQueue()
        critical = walk_to_hub(make_order())
        queue.place(critical)
        queue.result("LAB-001", 7.2, T0 + timedelta(hours=6))  # critical high K
        self.assertEqual(len(queue.critical_results()), 1)
        critical.advance(SampleState.DELIVERED, T0 + timedelta(hours=8))
        self.assertEqual(queue.critical_results(), [])

    def test_normal_results_not_surfaced(self):
        queue = LabQueue()
        queue.place(walk_to_hub(make_order()))
        queue.result("LAB-001", 4.1, T0 + timedelta(hours=6))
        self.assertEqual(queue.critical_results(), [])

    def test_turnaround_hours(self):
        order = walk_to_hub(make_order())
        order.record_result(4.1, T0 + timedelta(hours=6))
        self.assertAlmostEqual(order.turnaround_hours(), 5.0)

    def test_turnaround_none_before_result(self):
        self.assertIsNone(make_order().turnaround_hours())


if __name__ == "__main__":
    unittest.main()
