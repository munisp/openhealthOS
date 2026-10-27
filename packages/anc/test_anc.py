import unittest
from anc import PregnancyRegister, ANCError, CONTACTS


class TestANC(unittest.TestCase):
    def setUp(self):
        self.r = PregnancyRegister()
        # LMP at day 0, register at day 70 = 10 weeks (on-time booking)
        self.r.register("preg-1", lmp_day=0, day=70, facility="phc-1")

    def test_schedule_has_eight_contacts(self):
        self.assertEqual(len(CONTACTS), 8)

    def test_late_booking_flagged(self):
        r = PregnancyRegister()
        p = r.register("preg-late", lmp_day=0, day=119, facility="phc-1")  # 17w
        self.assertTrue(p["late_booking"])

    def test_contact_on_time_and_window(self):
        rec = self.r.contact("preg-1", 1, day=77, cadre="nurse", bp=(110, 70),
                             hiv_tested=True, tt_dose=1)
        self.assertTrue(rec["on_time"])
        self.assertFalse(rec["escalate"])
        rec2 = self.r.contact("preg-1", 2, day=161, cadre="nurse", bp=(118, 76))
        self.assertEqual(rec2["ga_weeks"], 23)
        self.assertFalse(rec2["on_time"])  # 23w is contact 3's window

    def test_danger_sign_escalates(self):
        rec = self.r.contact("preg-1", 1, day=77, cadre="chw",
                             danger_signs=["vaginal-bleeding"])
        self.assertTrue(rec["escalate"])
        self.assertIn("vaginal-bleeding", rec["escalation_reasons"])

    def test_bp_flag_escalates_with_pregnancy_context(self):
        rec = self.r.contact("preg-1", 1, day=77, cadre="nurse",
                             bp=(150, 95))
        self.assertTrue(rec["escalate"])
        self.assertTrue(any("hypertension-in-pregnancy" in x
                            for x in rec["escalation_reasons"]))

    def test_implausible_bp_and_unknown_sign_refused(self):
        with self.assertRaises(ANCError):
            self.r.contact("preg-1", 1, day=77, cadre="nurse", bp=(400, 10))
        with self.assertRaises(ANCError):
            self.r.contact("preg-1", 1, day=77, cadre="nurse",
                           danger_signs=["bad-luck"])

    def test_duplicate_contact_refused(self):
        self.r.contact("preg-1", 1, day=77, cadre="nurse")
        with self.assertRaises(ANCError):
            self.r.contact("preg-1", 1, day=80, cadre="nurse")

    def test_maternal_death_requires_mpdsr_link(self):
        with self.assertRaises(ANCError):
            self.r.close("preg-1", day=100, outcome="maternal-death")
        p = self.r.close_maternal_death("preg-1", day=100,
                                        mpdsr_death_id="mpdsr-41")
        self.assertEqual(p["outcome"]["mpdsr_death_id"], "mpdsr-41")
        with self.assertRaises(ANCError):
            self.r.contact("preg-1", 2, day=105, cadre="nurse")

    def test_overdue_contacts_earliest_first(self):
        od = self.r.overdue_contacts(as_of=200)  # ~28w, no contacts at all
        self.assertEqual(len(od), 1)
        self.assertEqual(od[0]["contact"], 1)
        self.r.contact("preg-1", 1, day=77, cadre="nurse")
        od = self.r.overdue_contacts(as_of=200)
        self.assertEqual(od[0]["contact"], 2)

    def test_stats_denominators(self):
        self.r.contact("preg-1", 1, day=77, cadre="nurse", hiv_tested=True)
        self.r.register("preg-2", lmp_day=0, day=119, facility="phc-1")
        s = self.r.stats(as_of=120)
        self.assertEqual(s["registered"], 2)
        self.assertEqual(s["late_bookings"], 1)
        self.assertEqual(s["hiv_tested_at_least_once"], 1)

    def test_implausible_ga_refused(self):
        with self.assertRaises(ANCError):
            self.r.register("preg-x", lmp_day=10, day=5, facility="phc-1")
        with self.assertRaises(ANCError):
            self.r.register("preg-y", lmp_day=0, day=400, facility="phc-1")


if __name__ == "__main__":
    unittest.main()
