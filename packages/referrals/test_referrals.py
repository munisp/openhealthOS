import unittest
from referrals import ReferralRegister, ReferralError


class TestReferrals(unittest.TestCase):
    def setUp(self):
        self.r = ReferralRegister()
        self.ref = self.r.create(
            patient_id="p1", from_facility="phc-a", to_facility="gen-b",
            reason="severe-pneumonia", urgency="emergency", day=100,
            pre_referral_treatment="first-dose-antibiotic")

    def test_happy_path_closes_loop(self):
        rid = self.ref["id"]
        self.r.dispatch(rid, day=100, transport="ambulance")
        rec = self.r.receive(rid, day=100, receiving_clinician="dr-x")
        self.assertEqual(rec["state"], "received")
        out = self.r.record_outcome(rid, day=102, diagnosis="severe pneumonia",
                                    treatment="IV antibiotics",
                                    feedback="improved; continue amoxicillin",
                                    counter_referral=True)
        self.assertTrue(out["outcome"]["counter_referral"])

    def test_strict_state_machine(self):
        rid = self.ref["id"]
        with self.assertRaises(ReferralError):
            self.r.receive(rid, day=100, receiving_clinician="dr-x")
        self.r.dispatch(rid, day=100)
        with self.assertRaises(ReferralError):
            self.r.dispatch(rid, day=101)
        with self.assertRaises(ReferralError):
            self.r.record_outcome(rid, day=101)

    def test_emergency_overdue_after_one_day(self):
        rid = self.ref["id"]
        self.r.dispatch(rid, day=100, transport="family-carried")
        od = self.r.overdue(as_of=102)
        self.assertEqual(len(od), 1)
        self.assertEqual(od[0]["days_overdue"], 1)

    def test_routine_window_longer(self):
        # close the setUp emergency referral so only the routine one is open
        self.r.dispatch(self.ref["id"], day=100)
        self.r.receive(self.ref["id"], day=100, receiving_clinician="dr-x")
        r2 = self.r.create(patient_id="p2", from_facility="phc-a",
                           to_facility="gen-b", reason="chronic-review",
                           urgency="routine", day=100)
        self.r.dispatch(r2["id"], day=100)
        self.assertEqual(self.r.overdue(as_of=106), [])
        self.assertEqual(len(self.r.overdue(as_of=108)), 1)

    def test_refusal_recorded_and_reroutable(self):
        rid = self.ref["id"]
        self.r.dispatch(rid, day=100)
        r = self.r.refuse(rid, day=100, reason="no-oxygen")
        self.assertEqual(r["state"], "refused-by-receiving")
        r = self.r.reroute(rid, to_facility="gen-c", day=100)
        self.assertEqual(r["to"], "gen-c")
        self.assertEqual(r["state"], "dispatched")

    def test_self_referral_and_bad_urgency_refused(self):
        with self.assertRaises(ReferralError):
            self.r.create(patient_id="p", from_facility="a", to_facility="a",
                          reason="x", urgency="emergency", day=1)
        with self.assertRaises(ReferralError):
            self.r.create(patient_id="p", from_facility="a", to_facility="b",
                          reason="x", urgency="whenever", day=1)

    def test_stats_denominators(self):
        rid = self.ref["id"]
        self.r.dispatch(rid, day=100)
        self.r.receive(rid, day=100, receiving_clinician="dr-x")
        r2 = self.r.create(patient_id="p2", from_facility="phc-a",
                           to_facility="gen-b", reason="x",
                           urgency="emergency", day=100)
        self.r.dispatch(r2["id"], day=100)
        s = self.r.stats(as_of=105)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["received"], 1)
        self.assertEqual(s["overdue"], 1)
        self.assertEqual(s["emergencies_overdue"], 1)


if __name__ == "__main__":
    unittest.main()
