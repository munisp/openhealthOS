"""Tests for packages/emar — the medication administration record."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emar import (  # noqa: E402
    MAR, Error, register_high_alert, FREQUENCIES, HIGH_ALERT,
)


def dispensed_order(**kw):
    base = {
        "order_id": "ORD-1", "patient_ref": "Pax-1",
        "drug_code": "NG-AMOX500", "drug_class": "penicillin",
        "dose": "500mg", "route": "oral", "frequency": "tds",
        "state": "dispensed", "duration_days": 2,
        "medication_barcode": "MED-AMOX500",
    }
    base.update(kw)
    return base


class TestPlacement(unittest.TestCase):
    def test_undispensed_order_refused(self):
        mar = MAR("MAR-1", "Pax-1", "ADM-1", "WB-PAX1")
        with self.assertRaises(Error):
            mar.add_order(dispensed_order(state="placed"), day=1)

    def test_wrong_patient_refused(self):
        mar = MAR("MAR-1", "Pax-1", "ADM-1", "WB-PAX1")
        with self.assertRaises(Error):
            mar.add_order(dispensed_order(patient_ref="Pax-2"), day=1)

    def test_unknown_frequency_refused(self):
        mar = MAR("MAR-1", "Pax-1", "ADM-1", "WB-PAX1")
        with self.assertRaises(Error):
            mar.add_order(dispensed_order(frequency="whenever"), day=1)

    def test_schedule_shape(self):
        mar = MAR("MAR-1", "Pax-1", "ADM-1", "WB-PAX1")
        slots = mar.add_order(dispensed_order(), day=1)
        # tds x 2 days = 6 slots
        self.assertEqual(len(slots), 6)
        self.assertEqual(len(FREQUENCIES["tds"]), 3)


class TestAdministration(unittest.TestCase):
    def setUp(self):
        self.mar = MAR("MAR-1", "Pax-1", "ADM-1", "WB-PAX1")
        slots = self.mar.add_order(dispensed_order(duration_days=1), day=1)
        self.slot = slots[0]

    def test_given_within_window(self):
        self.mar.slots[self.slot].record(
            "given", by="nurse-ada", minute=8 * 60 + 10,
            patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")
        self.assertEqual(self.mar.slots[self.slot].state, "given")

    def test_wrong_patient_barcode_refused(self):
        with self.assertRaises(Error):
            self.mar.slots[self.slot].record(
                "given", by="nurse-ada", minute=8 * 60,
                patient_barcode="WB-WRONG",
                medication_barcode="MED-AMOX500")

    def test_wrong_medication_barcode_refused(self):
        with self.assertRaises(Error):
            self.mar.slots[self.slot].record(
                "given", by="nurse-ada", minute=8 * 60,
                patient_barcode="WB-PAX1", medication_barcode="MED-WRONG")

    def test_barcode_exception_is_data(self):
        self.mar.slots[self.slot].record(
            "given", by="nurse-ada", minute=8 * 60,
            barcode_exception="ward scanner offline — visual two-person check")
        ev = self.mar.slots[self.slot].event
        self.assertIn("scanner offline", ev["barcode_exception"])

    def test_outside_window_needs_reason(self):
        with self.assertRaises(Error):
            self.mar.slots[self.slot].record(
                "given", by="nurse-ada", minute=8 * 60 + 120,
                patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")
        self.mar.slots[self.slot].record(
            "given", by="nurse-ada", minute=8 * 60 + 120,
            reason="patient in radiology",
            patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")

    def test_refusal_needs_reason_and_is_terminal(self):
        with self.assertRaises(Error):
            self.mar.slots[self.slot].record(
                "refused", by="nurse-ada", minute=8 * 60)
        self.mar.slots[self.slot].record(
            "refused", by="nurse-ada", minute=8 * 60,
            reason="patient nauseated, refused oral dose")
        with self.assertRaises(Error):
            self.mar.slots[self.slot].record(
                "given", by="nurse-ada", minute=9 * 60,
                patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")


class TestHighAlert(unittest.TestCase):
    def test_high_alert_requires_witness(self):
        self.assertIn("insulins", HIGH_ALERT)
        mar = MAR("MAR-2", "Pax-1", "ADM-1", "WB-PAX1")
        slots = mar.add_order(dispensed_order(
            order_id="ORD-2", drug_code="NG-INSULIN",
            drug_class="insulins", frequency="bd", duration_days=1,
            medication_barcode="MED-INS"), day=1)
        with self.assertRaises(Error):
            mar.slots[slots[0]].record(
                "given", by="nurse-ada", minute=8 * 60,
                patient_barcode="WB-PAX1", medication_barcode="MED-INS")
        mar.slots[slots[0]].record(
            "given", by="nurse-ada", minute=8 * 60, witness="nurse-bola",
            patient_barcode="WB-PAX1", medication_barcode="MED-INS")
        self.assertEqual(mar.slots[slots[0]].event["witness"], "nurse-bola")

    def test_witness_cannot_be_giver(self):
        mar = MAR("MAR-3", "Pax-1", "ADM-1", "WB-PAX1")
        slots = mar.add_order(dispensed_order(
            order_id="ORD-3", drug_class="opioids", frequency="bd",
            duration_days=1, medication_barcode="MED-MORPH"), day=1)
        with self.assertRaises(Error):
            mar.slots[slots[0]].record(
                "given", by="nurse-ada", minute=8 * 60, witness="nurse-ada",
                patient_barcode="WB-PAX1", medication_barcode="MED-MORPH")

    def test_register_high_alert(self):
        register_high_alert("test-class-x")
        self.assertIn("test-class-x", HIGH_ALERT)


class TestSheetAndAudit(unittest.TestCase):
    def test_sheet_overdue_and_fingerprint(self):
        mar = MAR("MAR-4", "Pax-1", "ADM-1", "WB-PAX1")
        slots = mar.add_order(dispensed_order(duration_days=1), day=1)
        mar.slots[slots[0]].record(
            "given", by="nurse-ada", minute=8 * 60,
            patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")
        sheet = mar.sheet(1)
        self.assertEqual(len(sheet["rows"]), 3)
        overdue = [r for r in sheet["rows"] if r["overdue"]]
        self.assertEqual(len(overdue), 2)
        self.assertEqual(len(sheet["sha256"]), 64)
        audit = mar.audit(1)
        self.assertEqual(audit["given"], 1)
        self.assertEqual(audit["scheduled"], 2)
        self.assertEqual(audit["total"], 3)

    def test_discontinue_drops_future_slots(self):
        mar = MAR("MAR-5", "Pax-1", "ADM-1", "WB-PAX1")
        mar.add_order(dispensed_order(duration_days=3), day=1)
        dropped = mar.discontinue_order(
            "ORD-1", by="dr-eze", day=1, reason="rash — allergy suspected")
        self.assertEqual(dropped, 9)
        audit = mar.audit(2)
        self.assertEqual(audit["omitted"], 3)

    def test_prn_dose(self):
        mar = MAR("MAR-6", "Pax-1", "ADM-1", "WB-PAX1")
        mar.add_order(dispensed_order(
            order_id="ORD-P", frequency="prn", duration_days=1), day=1)
        with self.assertRaises(Error):
            mar.give_prn("ORD-P", by="nurse-ada", minute=600, day=1,
                         indication="",
                         patient_barcode="WB-PAX1",
                         medication_barcode="MED-AMOX500")
        slot_id, ev = mar.give_prn(
            "ORD-P", by="nurse-ada", minute=600, day=1,
            indication="pain 7/10",
            patient_barcode="WB-PAX1", medication_barcode="MED-AMOX500")
        self.assertEqual(ev["state"], "given")
        self.assertEqual(mar.slots[slot_id].state, "given")


if __name__ == "__main__":
    unittest.main()
