import unittest

from lis import Lab, Error


class TestLab(unittest.TestCase):
    def setUp(self):
        self.lab = Lab("FAC-1")

    def _to_result(self, test="potassium_mmol", value=3.9):
        o = self.lab.order("PAT-1", test, "dr-a", priority="stat")
        self.lab.collect(o.id, "phlebotomist-1")
        self.lab.start_analysis(o.id)
        self.lab.result(o.id, value, "mmol/L", entered_by="scientist-1")
        return o

    def test_happy_path_to_verified(self):
        o = self._to_result()
        self.assertEqual(o.state, "resulted")
        self.lab.verify(o.id, "scientist-2")
        self.assertEqual(o.state, "verified")
        self.assertFalse(o.critical)

    def test_no_result_without_sample(self):
        o = self.lab.order("PAT-1", "potassium_mmol", "dr-a")
        with self.assertRaises(Error):
            self.lab.result(o.id, 3.9, "mmol/L", "scientist-1")

    def test_four_eyes_verification(self):
        o = self._to_result()
        with self.assertRaises(Error) as ctx:
            self.lab.verify(o.id, "scientist-1")
        self.assertIn("four-eyes", str(ctx.exception))

    def test_critical_result_flow(self):
        o = self._to_result(value=7.8)  # potassium critical high
        self.assertTrue(o.critical)
        self.lab.verify(o.id, "scientist-2")
        self.assertIn(o.id, self.lab.unacknowledged_criticals())
        self.lab.acknowledge_critical(o.id, "dr-a")
        self.assertNotIn(o.id, self.lab.unacknowledged_criticals())
        self.assertIsNotNone(self.lab.critical_ack_latency())

    def test_ack_only_after_verify(self):
        o = self._to_result(value=7.8)
        with self.assertRaises(Error):
            self.lab.acknowledge_critical(o.id, "dr-a")

    def test_text_results_allowed(self):
        o = self.lab.order("PAT-1", "malaria_rdt", "dr-a")
        self.lab.collect(o.id, "phleb-1")
        self.lab.start_analysis(o.id)
        self.lab.result(o.id, "positive", "", "scientist-1")
        self.assertFalse(o.critical)  # no numeric critical limits for RDT

    def test_worklist_stat_first(self):
        self.lab.order("P1", "fbc", "dr-a", "routine")
        stat = self.lab.order("P2", "glucose_mmol", "dr-a", "stat")
        wl = self.lab.worklist()
        self.assertEqual(wl[0]["order"], stat.id)

    def test_cancel_rules(self):
        o = self._to_result()
        self.lab.verify(o.id, "scientist-2")
        with self.assertRaises(Error):
            self.lab.cancel(o.id, "dr-a", "duplicate order")

    def test_order_requires_clinician(self):
        with self.assertRaises(Error):
            self.lab.order("P1", "fbc", "")


if __name__ == "__main__":
    unittest.main()
