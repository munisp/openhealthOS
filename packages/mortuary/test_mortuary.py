"""Tests for packages/mortuary — deceased register and chain of custody."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mortuary import ColdRoom, MortuaryRegister, Error  # noqa: E402


class TestColdRoom(unittest.TestCase):
    def test_assign_and_full(self):
        room = ColdRoom("R1", 2)
        self.assertEqual(room.assign("B1"), 1)
        self.assertEqual(room.assign("B2"), 2)
        with self.assertRaises(Error):
            room.assign("B3")

    def test_no_double_slot(self):
        room = ColdRoom("R1", 4)
        room.assign("B1")
        with self.assertRaises(Error):
            room.assign("B1")

    def test_move_and_release(self):
        room = ColdRoom("R1", 4)
        room.assign("B1")
        room.move("B1", 3)
        self.assertEqual(room.slots[3], "B1")
        room.release("B1")
        self.assertEqual(room.occupancy()["free"], 4)


class TestMortuaryRegister(unittest.TestCase):
    def setUp(self):
        self.reg = MortuaryRegister("FAC-1")
        self.room = ColdRoom("R1", 4)

    def test_declare_death_named(self):
        with self.assertRaises(Error):
            self.reg.declare_death(patient_ref="Pax-1", declared_by="",
                                   day=1, minute=600, cause_stated="x")
        with self.assertRaises(Error):
            self.reg.declare_death(patient_ref="Pax-1", declared_by="dr",
                                   day=1, minute=600, cause_stated="")

    def test_full_chain_of_custody(self):
        body = self.reg.declare_death(
            patient_ref="Pax-1", declared_by="dr-eze", day=1, minute=600,
            cause_stated="septic shock")
        slot = self.reg.admit_to_cold_room(body, self.room, by="orderly-1",
                                           day=1, minute=620)
        self.assertEqual(slot, 1)
        self.reg.transfer(body, self.room, 2, by="orderly-1", day=1,
                          minute=700)
        rel = self.reg.release(body, self.room, released_to="Mrs Ada Okafor",
                               relationship="spouse",
                               document="next-of-kin-letter",
                               by="mortuary-admin", day=2, minute=900)
        self.assertEqual(rel["document"], "next-of-kin-letter")
        self.assertEqual(len(self.reg.bodies[body]["custody"]), 4)

    def test_release_requires_document(self):
        body = self.reg.declare_death(
            patient_ref="Pax-1", declared_by="dr-eze", day=1, minute=600,
            cause_stated="RTA")
        self.reg.admit_to_cold_room(body, self.room, by="o", day=1,
                                    minute=620)
        with self.assertRaises(Error):
            self.reg.release(body, self.room, released_to="x",
                             relationship="brother", document="verbal",
                             by="admin", day=2, minute=900)

    def test_released_body_cannot_move(self):
        body = self.reg.declare_death(
            patient_ref="Pax-1", declared_by="dr-eze", day=1, minute=600,
            cause_stated="x")
        self.reg.admit_to_cold_room(body, self.room, by="o", day=1,
                                    minute=620)
        self.reg.release(body, self.room, released_to="family",
                         relationship="son", document="burial-permit",
                         by="admin", day=2, minute=900)
        with self.assertRaises(Error):
            self.reg.transfer(body, self.room, 2, by="o", day=3,
                              minute=100)

    def test_register_snapshot(self):
        self.reg.declare_death(patient_ref="Pax-1", declared_by="dr",
                               day=1, minute=1, cause_stated="x")
        self.reg.declare_death(patient_ref="Pax-2", declared_by="dr",
                               day=1, minute=2, cause_stated="y")
        snap = self.reg.register_snapshot()
        self.assertEqual(snap["in_custody"],
                         ["FAC-1-BODY-1", "FAC-1-BODY-2"])
        self.assertEqual(len(snap["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
