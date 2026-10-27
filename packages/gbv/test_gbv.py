import unittest
from gbv import ServiceDirectory, CaseBook, GBVError


class TestGBV(unittest.TestCase):
    def setUp(self):
        self.d = ServiceDirectory()
        self.d.register("svc-psy", kind="psychosocial", name="NGO-Care",
                        location="ward-3", contact="080...", verified_day=10)
        self.d.register("svc-old", kind="legal", name="LegalAid",
                        location="town", contact="081...", verified_day=1)
        self.b = CaseBook(self.d)
        self.case = self.b.open_case(
            officer="nurse-a", officer_role="gbv-focal-point", day=100,
            safety="unsafe-at-home",
            care_team=["gbv-focal-point", "gbv-counsellor"])

    def test_only_sealed_roles_open_cases(self):
        with self.assertRaises(GBVError):
            self.b.open_case(officer="clerk", officer_role="records",
                             day=100, safety="safe-at-home",
                             care_team=["gbv-focal-point"])

    def test_safety_assessment_required(self):
        with self.assertRaises(GBVError):
            self.b.open_case(officer="n", officer_role="gbv-clinician",
                             day=100, safety=None,
                             care_team=["gbv-clinician"])

    def test_sealed_records_deny_outsiders_and_log(self):
        with self.assertRaises(GBVError):
            self.b.read_case(self.case["case_id"], accessor="clerk",
                             accessor_role="records", day=101)
        self.assertFalse(self.b.access_log[-1]["granted"])
        ok = self.b.read_case(self.case["case_id"], accessor="nurse-a",
                              accessor_role="gbv-focal-point", day=101)
        self.assertEqual(ok["safety"], "unsafe-at-home")
        self.assertTrue(self.b.access_log[-1]["granted"])

    def test_referral_requires_consent(self):
        with self.assertRaises(GBVError):
            self.b.refer(self.case["case_id"], "svc-psy", day=101,
                         by="nurse-a", by_role="gbv-focal-point",
                         consented=False)

    def test_stale_service_flagged_not_hidden(self):
        rec = self.b.refer(self.case["case_id"], "svc-old", day=200,
                           by="nurse-a", by_role="gbv-focal-point",
                           consented=True)
        self.assertFalse(rec["service_verified"])
        self.assertIn("stale", rec["warning"])

    def test_referral_by_non_team_refused(self):
        with self.assertRaises(GBVError):
            self.b.refer(self.case["case_id"], "svc-psy", day=101,
                         by="dr-b", by_role="gbv-clinician", consented=True)

    def test_unknown_service_refused(self):
        with self.assertRaises(GBVError):
            self.b.refer(self.case["case_id"], "svc-ghost", day=101,
                         by="nurse-a", by_role="gbv-focal-point",
                         consented=True)

    def test_aggregate_has_no_linelist(self):
        self.b.refer(self.case["case_id"], "svc-psy", day=101,
                     by="nurse-a", by_role="gbv-focal-point",
                     consented=True)
        agg = self.b.aggregate()
        self.assertEqual(agg["cases"], 1)
        self.assertEqual(agg["unsafe_at_home"], 1)
        self.assertEqual(agg["referrals_made"], 1)
        self.assertNotIn("notes", agg)
        self.assertNotIn("survivors", agg)


if __name__ == "__main__":
    unittest.main()
