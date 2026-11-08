"""Tests for the formulary expansion pack (NG + CV registry entries).

The expansion is DATA loaded through Formulary.load_extra — the doctrine
that every country and drug is a registry entry, never a code branch.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from formulary import Formulary  # noqa: E402

EXTRA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "formulary_expansion.json")


class TestExpansionPack(unittest.TestCase):
    def setUp(self):
        self.f = Formulary()
        self.n_before = len(self.f.drugs)
        self.f.load_extra(EXTRA)

    def test_expansion_loads(self):
        with open(EXTRA) as fh:
            pack = json.load(fh)
        self.assertEqual(len(self.f.drugs),
                         self.n_before + len(pack["drugs"]))
        self.assertGreaterEqual(len(self.f.drugs), 80)

    def test_cv_entries_present_and_searchable(self):
        self.assertIsNotNone(self.f.lookup("CV-METF850"))
        self.assertIsNotNone(self.f.lookup("CV-ENAL10"))
        hits = self.f.search("metform")
        names = {h["name"] for h in hits}
        self.assertIn("Metformin", names)
        self.assertIn("Metformina", names)

    def test_codes_unique(self):
        with open(EXTRA) as fh:
            pack = json.load(fh)
        codes = [d["code"] for d in pack["drugs"]]
        self.assertEqual(len(codes), len(set(codes)))

    def test_expanded_interactions_fire(self):
        # benzodiazepine + opioid is contraindicated in the expansion
        findings = self.f.check_interactions(["NG-DIAZ10", "NG-MORPH10"])
        self.assertTrue(any("respiratory" in str(x).lower()
                            or "contraindicat" in str(x).lower()
                            for x in findings))

    def test_new_dose_rules_active(self):
        # warfarin expansion dose rule present for both NG and CV codes
        self.assertIn("NG-WARF5", self.f.dose_rules)
        self.assertIn("CV-WARF5", self.f.dose_rules)
        self.assertIn("NG-GENT80", self.f.dose_rules)

    def test_cv_allergy_check_uses_same_engine(self):
        from formulary import AllergyConflict
        with self.assertRaises(AllergyConflict):
            self.f.check_allergy("CV-AMOX500", ["penicillin"])

    def test_classes_resolve_through_drugkb(self):
        # classes used in the pack must canonicalize through the one home
        sys.path.insert(0, os.path.join(os.path.dirname(
            os.path.abspath(__file__)), "..", "drugkb"))
        from drugkb import canonical_class
        with open(EXTRA) as fh:
            pack = json.load(fh)
        for d in pack["drugs"]:
            canon = canonical_class(d["class"])
            self.assertIsInstance(canon, str)
            self.assertTrue(canon)


if __name__ == "__main__":
    unittest.main()
