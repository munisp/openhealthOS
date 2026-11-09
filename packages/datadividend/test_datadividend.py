
import unittest
from datadividend import (ConsentScopes, DataUseLedger, DividendError,
                          K_FLOOR)


def _ledger():
    c = ConsentScopes()
    c.grant("p1", purpose="research", consent_ref="consent-sig-1")
    c.grant("p1", purpose="registry", consent_ref="consent-sig-2")
    return DataUseLedger(c)


class TestDataDividend(unittest.TestCase):
    def test_use_without_scope_refused(self):
        led = _ledger()
        with self.assertRaises(DividendError):
            led.record_use("p1", purpose="marketing", study_ref="s1",
                           cohort_size=100, day=1)

    def test_small_cohort_refused(self):
        led = _ledger()
        with self.assertRaises(DividendError):
            led.record_use("p1", purpose="research", study_ref="s1",
                           cohort_size=K_FLOOR - 1, day=1)

    def test_valid_use_ledgers_with_checksum(self):
        led = _ledger()
        cs = led.record_use("p1", purpose="research", study_ref="MAL-01",
                            cohort_size=400, day=1,
                            outcome="changed first-line malaria guidance")
        self.assertTrue(cs.startswith("") and len(cs) == 64)
        self.assertTrue(led.verify())

    def test_tamper_detected(self):
        led = _ledger()
        led.record_use("p1", purpose="research", study_ref="s1",
                       cohort_size=50, day=1)
        led._entries[0]["cohort_size"] = 5  # attacker edits history
        with self.assertRaises(DividendError):
            led.verify()

    def test_statement_plain_language(self):
        led = _ledger()
        led.record_use("p1", purpose="research", study_ref="MAL-01",
                       cohort_size=400, day=1,
                       outcome="changed first-line malaria guidance")
        led.record_use("p1", purpose="registry", study_ref="NCR",
                       cohort_size=9000, day=2)
        st = led.patient_statement("p1")
        self.assertEqual(st["uses"], 2)
        self.assertIn("malaria", st["statement"])

    def test_empty_statement_honest(self):
        led = _ledger()
        st = led.patient_statement("p1")
        self.assertEqual(st["uses"], 0)
        self.assertIn("has not been used", st["statement"])

    def test_consent_needs_artifact(self):
        c = ConsentScopes()
        with self.assertRaises(DividendError):
            c.grant("p1", purpose="research", consent_ref="")


if __name__ == "__main__":
    unittest.main()
