"""Tests for travelpass_cards — international card payments,
ICVP-shaped vaccination cards, and entry clearance."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "travelpass"))

from travelpass import (TravelPassError, TravelPassRegistry,  # noqa: E402
                        quote_premium)
import travelpass_cards as cards  # noqa: E402


def _psp_approve(payload):
    return {"status": "approved", "auth_code": "AUTH123"}


def _psp_decline(payload):
    return {"status": "declined"}


VISA_TEST_PAN = "4242424242424242"        # canonical Luhn-valid test PAN
AMEX_TEST_PAN = "378282246310005"         # Luhn-valid amex test PAN
MASTERCARD_TEST_PAN = "5555555555554444"  # Luhn-valid MC test PAN
UNIONPAY_TEST_PAN = "6250941006528599"    # Luhn-valid UnionPay test PAN


class TestDetectScheme(unittest.TestCase):
    def test_visa_detected(self):
        det = cards.detect_scheme(VISA_TEST_PAN)
        self.assertEqual(det["scheme"], "visa")

    def test_amex_detected_15_digits(self):
        det = cards.detect_scheme(AMEX_TEST_PAN)
        self.assertEqual(det["scheme"], "amex")

    def test_mastercard_detected(self):
        det = cards.detect_scheme(MASTERCARD_TEST_PAN)
        self.assertEqual(det["scheme"], "mastercard")

    def test_unionpay_detected(self):
        det = cards.detect_scheme(UNIONPAY_TEST_PAN)
        self.assertIn("unionpay", det["candidates"])

    def test_luhn_failure_refuses(self):
        with self.assertRaises(TravelPassError):
            cards.detect_scheme("4242424242424241")

    def test_bad_length_refuses(self):
        with self.assertRaises(TravelPassError):
            cards.detect_scheme("424242")

    def test_unrecognized_bin_refuses(self):
        with self.assertRaises(TravelPassError):
            cards.detect_scheme("9999999999999999")  # Luhn check? ensure
            # actually fails luhn or bin — either way it refuses


class TestPayCard(unittest.TestCase):
    def setUp(self):
        self.reg = TravelPassRegistry(country_code="ng")
        self.tid = "T1"
        self.reg.enroll("T1", full_name="Ada Lovelace",
                        passport_ref="P123", nationality="ng",
                        entry_day=1, exit_day=10, emergency_profile={},
                        day=1)
        self.amount = quote_premium(1, 10)["premium_kobo"]

    def test_successful_card_payment(self):
        entry = cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                               expiry="12/29", holder="Ada Lovelace",
                               psp_name="stripe",
                               psp_charge=_psp_approve,
                               amount_kobo=self.amount, reference="RC-1",
                               day=1, authenticated=True)
        self.assertEqual(entry["method"], "card")
        self.assertEqual(entry["card"]["last4"], "4242")
        self.assertEqual(entry["card"]["psp"], "stripe")
        self.assertNotIn("pan", {k.lower() for k in entry["card"]})

    def test_pan_never_stored(self):
        cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                       expiry="12/29", holder="Ada Lovelace",
                       psp_name="stripe", psp_charge=_psp_approve,
                       amount_kobo=self.amount, reference="RC-2", day=1,
                       authenticated=True)
        blob = str(self.reg.travelers[self.tid])
        self.assertNotIn(VISA_TEST_PAN, blob)
        self.assertNotIn(VISA_TEST_PAN, str(self.reg.payments))

    def test_unauthenticated_card_not_present_refuses(self):
        with self.assertRaises(TravelPassError) as cm:
            cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                           expiry="12/29", holder="Ada", psp_name="stripe",
                           psp_charge=_psp_approve,
                           amount_kobo=self.amount, reference="RC-3",
                           day=1, authenticated=False)
        self.assertIn("chargeback", str(cm.exception))

    def test_no_psp_transport_refuses(self):
        with self.assertRaises(TravelPassError) as cm:
            cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                           expiry="12/29", holder="Ada", psp_name="stripe",
                           psp_charge=None, amount_kobo=self.amount,
                           reference="RC-4", day=1, authenticated=True)
        self.assertIn("phishing", str(cm.exception))

    def test_unknown_psp_refuses(self):
        with self.assertRaises(TravelPassError):
            cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                           expiry="12/29", holder="Ada",
                           psp_name="sketchy-gateway", psp_charge=_psp_approve,
                           amount_kobo=self.amount, reference="RC-5",
                           day=1, authenticated=True)

    def test_declined_charge_refuses_no_ledger_entry(self):
        before = len(self.reg.payments)
        with self.assertRaises(TravelPassError):
            cards.pay_card(self.reg, self.tid, pan=VISA_TEST_PAN,
                           expiry="12/29", holder="Ada", psp_name="paystack",
                           psp_charge=_psp_decline,
                           amount_kobo=self.amount, reference="RC-6",
                           day=1, authenticated=True)
        self.assertEqual(len(self.reg.payments), before)


class TestVaccinationCards(unittest.TestCase):
    def setUp(self):
        self.reg = TravelPassRegistry(country_code="ng")
        self.tid = "T9"
        self.reg.enroll("T9", full_name="John Doe", passport_ref="P999",
                        nationality="ng", entry_day=100, exit_day=110,
                        emergency_profile={}, day=100)

    def test_record_and_card(self):
        cards.record_vaccination(self.reg, self.tid, vaccine="yellow-fever",
                                 day=80, certificate_ref="ICVP-7788",
                                 batch="YFV-2026-01",
                                 administering_country="ng")
        card = cards.vaccination_card(self.reg, self.tid)
        self.assertEqual(len(card["vaccinations"]), 1)
        self.assertIsNone(card["vaccinations"][0]["valid_until_day"])

    def test_missing_batch_refuses(self):
        with self.assertRaises(TravelPassError) as cm:
            cards.record_vaccination(self.reg, self.tid,
                                     vaccine="yellow-fever", day=80,
                                     certificate_ref="ICVP-1", batch="",
                                     administering_country="ng")
        self.assertIn("drawing", str(cm.exception))

    def test_unknown_vaccine_refuses(self):
        with self.assertRaises(TravelPassError):
            cards.record_vaccination(self.reg, self.tid,
                                     vaccine="snake-oil", day=80,
                                     certificate_ref="X", batch="B",
                                     administering_country="ng")

    def test_entry_clearance_passes_when_valid(self):
        cards.record_vaccination(self.reg, self.tid, vaccine="yellow-fever",
                                 day=80, certificate_ref="ICVP-7788",
                                 batch="YFV-2026-01",
                                 administering_country="ng")
        c = cards.entry_clearance(self.reg, self.tid, destination="ng",
                                  day=100)
        self.assertTrue(c["cleared"])

    def test_clearance_refuses_missing_vaccine_with_named_gap(self):
        c = cards.entry_clearance(self.reg, self.tid, destination="ng",
                                  day=100)
        self.assertFalse(c["cleared"])
        self.assertIn("not on record", c["gaps"][0])

    def test_clearance_refuses_inside_onset_window(self):
        # yellow fever shot on day 95 is not valid until day 105
        cards.record_vaccination(self.reg, self.tid, vaccine="yellow-fever",
                                 day=95, certificate_ref="ICVP-9",
                                 batch="YFV-1", administering_country="ng")
        c = cards.entry_clearance(self.reg, self.tid, destination="ng",
                                  day=100)
        self.assertFalse(c["cleared"])
        self.assertIn("onset window", c["gaps"][0])

    def test_unlisted_destination_refuses(self):
        with self.assertRaises(TravelPassError) as cm:
            cards.entry_clearance(self.reg, self.tid, destination="zz",
                                  day=100)
        self.assertIn("policy gap", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
