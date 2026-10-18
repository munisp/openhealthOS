#!/usr/bin/env python3
"""Family grouping tests: one head, kinship codes, guardian contact
routing, family-grouped CHW worklists, no double families."""
import unittest

from families import (Family, FamilyError, FamilyMember, FamilyRegister)


def build():
    r = FamilyRegister()
    r.create(Family("FAM-1", head_id="PT-HEAD", household_id="HH-9",
                    shared_phone="+234801", scheme="BHCPF-VUL"), actor="chw-1")
    r.add_member("FAM-1", FamilyMember("PT-WIFE", "spouse"), actor="chw-1")
    r.add_member("FAM-1", FamilyMember("PT-CHILD-1", "child", birth_year=2022), actor="chw-1")
    r.add_member("FAM-1", FamilyMember("PT-CHILD-2", "child", birth_year=2024), actor="chw-1")
    r.add_member("FAM-1", FamilyMember("PT-GRANNY", "parent"), actor="chw-1")
    return r


class TestStructure(unittest.TestCase):
    def test_one_head_and_default_guardian(self):
        r = build()
        f = r.get("FAM-1")
        heads = [m for m in f.members if m.relationship == "head"]
        self.assertEqual(len(heads), 1)
        kids = [m for m in f.members if m.relationship == "child"]
        self.assertTrue(all(m.guardian_id == "PT-HEAD" for m in kids))
        with self.assertRaises(FamilyError):
            r.add_member("FAM-1", FamilyMember("PT-X", "head"), actor="a")

    def test_patient_in_one_family_only(self):
        r = build()
        r.create(Family("FAM-2", head_id="PT-OTHER"), actor="a")
        with self.assertRaises(FamilyError):
            r.add_member("FAM-2", FamilyMember("PT-CHILD-1", "child"), actor="a")

    def test_named_actor_required(self):
        r = FamilyRegister()
        with self.assertRaises(FamilyError):
            r.create(Family("F", head_id="H"), actor="")

    def test_children_under_five(self):
        r = build()
        self.assertEqual(len(r.get("FAM-1").children_under(5, 2026)), 2)
        self.assertEqual(r.stats()["dependants"], 2)


class TestContactRouting(unittest.TestCase):
    def test_child_reminders_go_to_guardian(self):
        r = build()
        self.assertEqual(r.contact_for("PT-CHILD-1"), "PT-HEAD")
        self.assertEqual(r.contact_for("PT-WIFE"), "PT-WIFE")
        self.assertEqual(r.contact_for("PT-STRANGER"), "PT-STRANGER")

    def test_guardian_worklist_groups_by_family(self):
        r = build()
        due = [("PT-CHILD-1", "penta-2", "2026-04-01"),
               ("PT-CHILD-2", "bcg", "2026-04-02"),
               ("PT-GRANNY", "bp-check", "2026-04-03"),
               ("PT-LONER", "anc-visit", "2026-04-01")]
        work = r.guardian_worklist(due)
        fam_item = next(w for w in work if w["family_id"] == "FAM-1")
        # the CHW visits the family once for both children — and granny
        # routes to herself, same visit, same list
        self.assertEqual(len(fam_item["patients_due"]), 3)
        self.assertEqual(fam_item["shared_phone"], "+234801")
        self.assertEqual(fam_item["household_id"], "HH-9")
        kids = [p for p in fam_item["patients_due"] if p["patient_id"].startswith("PT-CHILD")]
        self.assertTrue(all(p["contact"] == "PT-HEAD" for p in kids))
        granny = next(p for p in fam_item["patients_due"] if p["patient_id"] == "PT-GRANNY")
        self.assertEqual(granny["contact"], "PT-GRANNY")
        loner = next(w for w in work if w["family_id"] is None)
        self.assertEqual(loner["patients_due"][0]["contact"], "PT-LONER")
        # family first — most patients per visit
        self.assertEqual(work[0]["family_id"], "FAM-1")


if __name__ == "__main__":
    unittest.main()
