"""test_mammoprog.py — the category decides the interval; BI-RADS 0
is a debt, not a negative; the audit shows every n."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mammoprog import MammoError, MammoProgram, MAMMO_PROGRAM_VERSION  # noqa: E402,E501


class TestScreening(unittest.TestCase):
    def test_pathway_follows_category(self):
        m = MammoProgram()
        s0 = m.record_screen("w0", day=0, birads="0", by="rad-1",
                             facility="f", image_ref="m://0")
        self.assertEqual(s0["pathway"]["kind"], "additional-imaging")
        self.assertEqual(s0["pathway"]["due_day"], 30)
        s1 = m.record_screen("w1", day=0, birads="1", by="rad-1",
                             facility="f", image_ref="m://1")
        self.assertEqual(s1["pathway"]["due_day"], 30 * 36)
        s3 = m.record_screen("w3", day=0, birads="3", by="rad-1",
                             facility="f", image_ref="m://3")
        self.assertEqual(s3["pathway"]["kind"], "short-interval")
        self.assertEqual(s3["pathway"]["due_day"], 180)
        s4 = m.record_screen("w4", day=0, birads="4", by="rad-1",
                             facility="f", image_ref="m://4")
        self.assertEqual(s4["pathway"]["kind"], "biopsy-referral")
        self.assertEqual(s4["pathway"]["due_day"], 14)

    def test_bad_category_and_unsigned_refuse(self):
        m = MammoProgram()
        with self.assertRaises(MammoError):
            m.record_screen("w", day=0, birads="7", by="rad-1",
                            facility="f", image_ref="m://x")
        with self.assertRaises(MammoError):
            m.record_screen("w", day=0, birads="1", by="",
                            facility="f", image_ref="m://x")
        with self.assertRaises(MammoError):
            m.record_screen("w", day=0, birads="1", by="rad-1",
                            facility="f", image_ref="")


class TestLoops(unittest.TestCase):
    def setUp(self):
        self.m = MammoProgram()
        self.m.record_screen("w-zero", day=0, birads="0", by="r",
                             facility="f", image_ref="m://0")
        self.m.record_screen("w-four", day=0, birads="4", by="r",
                             facility="f", image_ref="m://4")
        self.m.record_screen("w-one", day=0, birads="1", by="r",
                             facility="f", image_ref="m://1")

    def test_due_overdue_first_and_birads0_leads(self):
        due = self.m.due(as_of=40)
        self.assertEqual([d["patient_id"] for d in due],
                         ["w-four", "w-zero"])
        self.assertEqual(due[0]["overdue_days"], 26)

    def test_complete_wrong_step_is_a_crossed_wire(self):
        with self.assertRaises(MammoError):
            self.m.complete_pathway("w-zero", day=20,
                                    what="biopsy-referral")
        self.m.complete_pathway("w-zero", day=20,
                                what="additional-imaging")
        with self.assertRaises(MammoError):   # no double-close
            self.m.complete_pathway("w-zero", day=21,
                                    what="additional-imaging")
        due = self.m.due(as_of=40)
        self.assertNotIn("w-zero", {d["patient_id"] for d in due})

    def test_biopsy_outcome_only_for_biopsy_grades(self):
        with self.assertRaises(MammoError):
            self.m.record_biopsy_outcome("w-one", day=20,
                                         outcome="benign")
        self.m.record_biopsy_outcome("w-four", day=20,
                                     outcome="malignant")
        with self.assertRaises(MammoError):
            self.m.record_biopsy_outcome("w-four", day=21,
                                         outcome="benign")


class TestAudit(unittest.TestCase):
    def test_audit_with_denominators(self):
        m = MammoProgram()
        m.record_screen("w1", day=0, birads="1", by="r", facility="f",
                        image_ref="m://1")
        m.record_screen("w0", day=0, birads="0", by="r", facility="f",
                        image_ref="m://0")
        m.record_screen("w4", day=0, birads="4", by="r", facility="f",
                        image_ref="m://4")
        m.record_screen("w5", day=0, birads="5", by="r", facility="f",
                        image_ref="m://5")
        m.record_biopsy_outcome("w4", day=20, outcome="benign")
        m.record_biopsy_outcome("w5", day=20, outcome="malignant")
        a = m.audit()
        self.assertEqual(a["screens"], 4)
        self.assertEqual(a["recall_rate"], 0.25)
        self.assertEqual(a["biopsies_with_outcome"], 2)
        self.assertEqual(a["biopsy_ppv"], 0.5)
        self.assertEqual(a["cancers_detected"], 1)
        self.assertEqual(a["detection_per_1000"], 250.0)
        self.assertEqual(a["programme_version"], MAMMO_PROGRAM_VERSION)


if __name__ == "__main__":
    unittest.main()
