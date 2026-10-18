#!/usr/bin/env python3
"""Blood bank tests: compatibility matrix, reservation/transfusion
refusals, expiry sweep, FEFO order, named actors."""
import unittest

from bloodbank import (BloodBank, IncompatibleUnit, Unit, UnitError,
                       compatible)

DAY = "2026-05-01"


def unit(uid, group="O+", product="packed-rbc", expires="2026-06-01", fac="FAC-1"):
    return Unit(unit_id=uid, group=group, product=product,
                collected="2026-04-20", expires=expires, facility=fac)


class TestCompatibility(unittest.TestCase):
    def test_universal_donor_recipient(self):
        self.assertTrue(compatible("O-", "packed-rbc", "AB+"))
        self.assertTrue(compatible("AB-", "packed-rbc", "AB+"))
        self.assertFalse(compatible("AB+", "packed-rbc", "O-"))
        self.assertFalse(compatible("A+", "packed-rbc", "B+"))

    def test_plasma_conservative(self):
        self.assertTrue(compatible("A+", "ffp", "A+"))
        self.assertFalse(compatible("A+", "ffp", "B+"))


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.bb = BloodBank()
        self.bb.receive(unit("U1", "O-"), DAY, actor="tech-1")
        self.bb.receive(unit("U2", "A+"), DAY, actor="tech-1")

    def test_reserve_and_transfuse(self):
        u = self.bb.reserve("U1", "B+", "PT-1", DAY, actor="nurse-1")
        self.assertEqual(u.status, "reserved")
        u = self.bb.transfuse("U1", "B+", "PT-1", DAY, actor="nurse-1")
        self.assertEqual(u.status, "transfused")
        self.assertEqual(u.transfused_to, "PT-1")

    def test_incompatible_refused_at_reserve(self):
        with self.assertRaises(IncompatibleUnit):
            self.bb.reserve("U2", "O-", "PT-2", DAY, actor="nurse-1")

    def test_bedside_recheck_catches_wrong_group(self):
        self.bb.reserve("U2", "A+", "PT-3", DAY, actor="nurse-1")
        with self.assertRaises(IncompatibleUnit):
            self.bb.transfuse("U2", "B-", "PT-3", DAY, actor="nurse-1")

    def test_double_reservation_refused(self):
        self.bb.reserve("U1", "AB+", "PT-4", DAY, actor="n-1")
        with self.assertRaises(UnitError):
            self.bb.reserve("U1", "AB+", "PT-5", DAY, actor="n-2")

    def test_named_actor_required(self):
        with self.assertRaises(UnitError):
            self.bb.receive(unit("U3"), DAY, actor="")


class TestExpiry(unittest.TestCase):
    def test_sweep_and_worklist(self):
        bb = BloodBank()
        bb.receive(unit("E1", expires="2026-05-03"), DAY, actor="t")
        bb.receive(unit("E2", expires="2026-05-20"), DAY, actor="t")
        soon = bb.expiring_soon(DAY, within_days=7)
        self.assertEqual([u.unit_id for u in soon], ["E1"])
        n = bb.sweep_expired("2026-05-21")
        self.assertEqual(n, 2)
        self.assertEqual(bb.available(), [])

    def test_expired_unit_cannot_be_transfused(self):
        bb = BloodBank()
        bb.receive(unit("E3", expires="2026-05-02"), DAY, actor="t")
        with self.assertRaises(UnitError):
            bb.transfuse("E3", "O+", "PT-9", "2026-05-05", actor="n")

    def test_fefo_order(self):
        bb = BloodBank()
        bb.receive(unit("F2", expires="2026-06-10"), DAY, actor="t")
        bb.receive(unit("F1", expires="2026-05-15"), DAY, actor="t")
        self.assertEqual([u.unit_id for u in bb.available()], ["F1", "F2"])
        self.assertEqual(bb.stock_by_group()["O+"], 2)


if __name__ == "__main__":
    unittest.main()
