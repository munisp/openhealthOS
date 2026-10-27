import unittest
from surgicalsafety import (ChecklistSession, EmONCAudit, SurgicalError,
                            EmONCError, CHECKLIST, EMONC_BASIC,
                            EMONC_COMPREHENSIVE)


def _do_phase(s, phase):
    s.start_phase(phase)
    for item in CHECKLIST[phase]:
        s.answer(item, True)
    return s.complete_phase()


class TestChecklist(unittest.TestCase):
    def setUp(self):
        self.s = ChecklistSession("case-1", team_lead="dr-z", day=100)

    def test_full_checklist(self):
        for ph in ("sign-in", "time-out", "sign-out"):
            _do_phase(self.s, ph)
        self.assertTrue(self.s.report()["fully_complete"])

    def test_sequential_phases(self):
        with self.assertRaises(SurgicalError):
            self.s.start_phase("time-out")

    def test_incomplete_phase_refused(self):
        self.s.start_phase("sign-in")
        self.s.answer("identity-confirmed", True)
        with self.assertRaises(SurgicalError) as cm:
            self.s.complete_phase()
        self.assertIn("site-marked", str(cm.exception))

    def test_no_answer_requires_reason(self):
        self.s.start_phase("sign-in")
        with self.assertRaises(SurgicalError):
            self.s.answer("antibiotic-prophylaxis-given"
                          if False else "allergy-known", False)
        # with reason, documented
        self.s.answer("allergy-known", False, reason="penicillin allergy — vancomycin planned")
        self.assertEqual(self.s.answers["sign-in"]["allergy-known"],
                         (False, "penicillin allergy — vancomycin planned"))

    def test_unknown_item_refused(self):
        self.s.start_phase("sign-in")
        with self.assertRaises(SurgicalError):
            self.s.answer("vibes-checked", True)


class TestEmONC(unittest.TestCase):
    def test_comprehensive_label_earned(self):
        a = EmONCAudit("gen-hosp")
        for i, sf in enumerate(EMONC_COMPREHENSIVE):
            a.record(sf, day=100 + i)
        out = a.audit(as_of=150)
        self.assertEqual(out["label"], "comprehensive-emonc")

    def test_basic_label(self):
        a = EmONCAudit("phc-1")
        for i, sf in enumerate(EMONC_BASIC):
            a.record(sf, day=100 + i)
        self.assertEqual(a.audit(as_of=150)["label"], "basic-emonc")

    def test_stale_functions_drop_the_label(self):
        a = EmONCAudit("gen-hosp")
        for i, sf in enumerate(EMONC_COMPREHENSIVE):
            a.record(sf, day=10 + i)
        out = a.audit(as_of=200)  # 190 days later, outside 90d window
        self.assertEqual(out["label"], "not-emonc")
        self.assertIn("caesarean", out["missing_comprehensive"])

    def test_partial_missing_named(self):
        a = EmONCAudit("phc-2")
        for sf in ("oxytocin", "antibiotics"):
            a.record(sf, day=100)
        out = a.audit(as_of=110)
        self.assertEqual(out["label"], "not-emonc")
        self.assertIn("neonatal-resuscitation", out["missing_basic"])

    def test_unknown_function_refused(self):
        with self.assertRaises(EmONCError):
            EmONCAudit("x").record("heart-transplant", day=1)


if __name__ == "__main__":
    unittest.main()
