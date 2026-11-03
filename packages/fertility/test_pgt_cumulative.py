import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from fertility import (FertilityClinic, FertilityError,  # noqa: E402
                       registry_return, cumulative_live_birth)


def make_clinic():
    cl = FertilityClinic("f")
    cl.register_couple("CPL", partner_a="ada", partner_b="obi", day=0)
    for kind in ("cycle-treatment", "embryo-transfer", "pgt"):
        for who in ("ada", "obi"):
            cl.grant_consent("CPL", kind, method="signed", by=who,
                             day=0)
    return cl


def cycle_to_culture(cl, cycle_id="CY1", couple="CPL", embryos=2,
                     start_day=1):
    cl.start_cycle(cycle_id, couple_id=couple, protocol_version="antag@2",
                   day=start_day)
    for stage in ("stimulation", "triggered", "retrieval",
                  "fertilization"):
        cl.advance(cycle_id, stage, day=start_day + 1, by="dr-1")
    cl.fertilize(cycle_id, method="icsi", n_embryos=embryos,
                 day=start_day + 2, embryologist="em-1")
    cl.advance(cycle_id, "culture", day=start_day + 3, by="em-1")
    return cycle_id


class TestPGT(unittest.TestCase):
    def setUp(self):
        self.cl = make_clinic()
        cycle_to_culture(self.cl)
        self.e = "CY1-E1"

    def test_pgt_transfer_gate_full_path(self):
        self.cl.order_pgt(self.e, kind="pgt-a", day=5, by="em-1")
        # no result back: transfer refuses — the biopsy is a question
        with self.assertRaises(FertilityError) as ctx:
            self.cl.move_embryo(self.e, "transferred", day=6,
                                witness_1="em-1", witness_2="em-2")
        self.assertIn("no result back", str(ctx.exception))
        self.cl.record_pgt_result(self.e, result="euploid", day=6,
                                  lab_ref="GEN-1")
        out = self.cl.move_embryo(self.e, "transferred", day=7,
                                  witness_1="em-1", witness_2="em-2")
        self.assertEqual(out["stage"], "transferred")

    def test_aneuploid_never_transfers(self):
        self.cl.order_pgt(self.e, kind="pgt-a", day=5, by="em-1")
        self.cl.record_pgt_result(self.e, result="aneuploid", day=6,
                                  lab_ref="GEN-1")
        with self.assertRaises(FertilityError) as ctx:
            self.cl.move_embryo(self.e, "transferred", day=7,
                                witness_1="em-1", witness_2="em-2")
        self.assertIn("aneuploid", str(ctx.exception))

    def test_mosaic_needs_documented_override(self):
        self.cl.order_pgt(self.e, kind="pgt-a", day=5, by="em-1")
        self.cl.record_pgt_result(self.e, result="mosaic", day=6,
                                  lab_ref="GEN-1")
        with self.assertRaises(FertilityError):
            self.cl.move_embryo(self.e, "transferred", day=7,
                                witness_1="em-1", witness_2="em-2")
        out = self.cl.move_embryo(self.e, "transferred", day=7,
                                  witness_1="em-1", witness_2="em-2",
                                  pgt_override="counselled: 40% mosaic, "
                                               "patient elected")
        self.assertEqual(out["pgt"]["override"]["witnesses"],
                         ("em-1", "em-2"))

    def test_order_gates(self):
        with self.assertRaises(FertilityError):  # off-menu kind
            self.cl.order_pgt(self.e, kind="pgt-x", day=5, by="em-1")
        with self.assertRaises(FertilityError):  # result without order
            self.cl.record_pgt_result(self.e, result="euploid", day=5,
                                      lab_ref="GEN-1")
        self.cl.order_pgt(self.e, kind="pgt-m", day=5, by="em-1")
        with self.assertRaises(FertilityError):  # no lab ref
            self.cl.record_pgt_result(self.e, result="unaffected",
                                      day=6, lab_ref="")
        with self.assertRaises(FertilityError):  # double order
            self.cl.order_pgt(self.e, kind="pgt-a", day=6, by="em-1")

    def test_pgt_consent_required(self):
        cl = FertilityClinic("f")
        cl.register_couple("CPLX", partner_a="a", partner_b="b", day=0)
        for kind in ("cycle-treatment", "embryo-transfer"):
            for who in ("a", "b"):
                cl.grant_consent("CPLX", kind, method="signed", by=who,
                                 day=0)
        cycle_to_culture(cl, "CYX", "CPLX")
        with self.assertRaises(FertilityError) as ctx:
            cl.order_pgt("CYX-E1", kind="pgt-a", day=5, by="em-1")
        self.assertIn("pgt consent", str(ctx.exception))


class TestCumulativeLiveBirth(unittest.TestCase):
    def _closed_cycle(self, cl, cycle_id, couple, start_day, live_birth):
        cycle_to_culture(cl, cycle_id, couple, embryos=1,
                         start_day=start_day)
        cl.advance(cycle_id, "transfer", day=start_day + 8, by="dr-1")
        cl.advance(cycle_id, "outcome-pending", day=start_day + 10,
                   by="dr-1")
        cl.close_cycle(cycle_id, day=start_day + 20,
                       pregnancy_test=live_birth,
                       clinical_pregnancy=live_birth,
                       live_birth=live_birth, singleton=live_birth)

    def test_cumulative_curve_with_denominators(self):
        cl = make_clinic()
        # CPL: fails cycle 1, succeeds cycle 2
        self._closed_cycle(cl, "CY1", "CPL", 1, False)
        self._closed_cycle(cl, "CY2", "CPL", 40, True)
        # couple 2: one cycle only, fails
        cl.register_couple("CPL2", partner_a="x", partner_b="y", day=0)
        for who in ("x", "y"):
            cl.grant_consent("CPL2", "cycle-treatment",
                             method="signed", by=who, day=0)
        self._closed_cycle(cl, "CYB", "CPL2", 1, False)
        out = cumulative_live_birth(cl)
        self.assertEqual(out["by_cycle"][1]["couples_starting"], 2)
        self.assertEqual(out["by_cycle"][1]["couples_with_live_birth"],
                         0)
        self.assertEqual(out["by_cycle"][2]["rate"], 1.0)
        self.assertEqual(out["by_cycle"][2]["couples_starting"], 1)
        # and it rides the registry return
        rr = registry_return(cl)
        self.assertIn("cumulative_live_birth", rr)
        self.assertEqual(rr["cumulative_live_birth"]["by_cycle"][2]
                         ["couples_starting"], 1)

    def test_empty_clinic_abstains(self):
        cl = FertilityClinic("f")
        out = cumulative_live_birth(cl)
        self.assertEqual(out["by_cycle"], {})


if __name__ == "__main__":
    unittest.main()
