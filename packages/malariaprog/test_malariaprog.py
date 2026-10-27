import unittest
from malariaprog import (SMCCampaign, LLINDistribution, TPRWatch,
                         MalariaError)


class TestSMC(unittest.TestCase):
    def setUp(self):
        self.c = SMCCampaign(2026)
        self.c.register_eligible("k1", age_months=12)
        self.c.register_eligible("k2", age_months=40)

    def test_age_gate_fail_closed(self):
        with self.assertRaises(MalariaError):
            self.c.register_eligible("baby", age_months=2)
        with self.assertRaises(MalariaError):
            self.c.register_eligible("big", age_months=60)

    def test_dose_and_double_dose_refused(self):
        self.c.give_dose("k1", 1, day=200)
        with self.assertRaises(MalariaError):
            self.c.give_dose("k1", 1, day=201)
        with self.assertRaises(MalariaError):
            self.c.give_dose("k1", 5, day=201)
        with self.assertRaises(MalariaError):
            self.c.give_dose("stranger", 1, day=201)

    def test_coverage_and_worklist(self):
        self.c.give_dose("k1", 1, day=200)
        cov = self.c.cycle_coverage(1)
        self.assertEqual((cov["eligible"], cov["dosed"], cov["missed"]),
                         (2, 1, 1))
        wl = self.c.next_cycle_worklist(2)
        self.assertEqual(len(wl), 1)
        self.assertEqual(wl[0]["child_id"], "k2")


class TestLLIN(unittest.TestCase):
    def test_distribution_register(self):
        l = LLINDistribution()
        l.distribute("hh-1", 2, day=100, distributor="chw-9")
        l.distribute("hh-1", 1, day=200, distributor="chw-9")
        l.distribute("hh-2", 3, day=100, distributor="chw-9")
        rep = l.report()
        self.assertEqual(rep["households_reached"], 2)
        self.assertEqual(rep["nets_distributed"], 6)

    def test_zero_nets_refused(self):
        with self.assertRaises(MalariaError):
            LLINDistribution().distribute("hh", 0, day=1, distributor="x")


class TestTPR(unittest.TestCase):
    def setUp(self):
        self.w = TPRWatch()
        # baseline weeks ~20% positivity
        for wk in range(1, 5):
            self.w.record("phc-1", wk, tested=100, confirmed=20)

    def test_impossible_counts_refused(self):
        with self.assertRaises(MalariaError):
            self.w.record("phc-1", 5, tested=10, confirmed=11)
        with self.assertRaises(MalariaError):
            self.w.record("phc-1", 5, tested=-1, confirmed=0)

    def test_small_numbers_are_noise(self):
        self.w.record("phc-2", 5, tested=8, confirmed=8)
        out = self.w.check_spike("phc-2", 5)
        self.assertIsNone(out["signal"])
        self.assertEqual(out["reason"], "insufficient-tests")

    def test_spike_detected_against_baseline(self):
        self.w.record("phc-1", 5, tested=100, confirmed=45)
        out = self.w.check_spike("phc-1", 5)
        self.assertEqual(out["signal"], "tpr-spike")
        self.assertEqual(out["route"], "ebs-inbox-as-rumour-not-alert")

    def test_no_spike_normal_week(self):
        self.w.record("phc-1", 5, tested=100, confirmed=25)
        out = self.w.check_spike("phc-1", 5)
        self.assertIsNone(out["signal"])
        self.assertEqual(out["tpr"], 0.25)

    def test_tpr_missing_week_refused(self):
        with self.assertRaises(MalariaError):
            self.w.tpr("phc-1", 99)


if __name__ == "__main__":
    unittest.main()
