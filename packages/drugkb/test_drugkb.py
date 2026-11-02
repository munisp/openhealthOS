import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from drugkb import (FormularyError, FORMULARY_VERSION, check_order,
                    classes_of, renal_verdict, RENAL_RULES_VERSION)


class TestClasses(unittest.TestCase):
    def test_class_membership_and_unmapped(self):
        self.assertEqual(classes_of("Ibuprofen"), {"nsaid"})
        self.assertEqual(classes_of("gentamicin"), {"aminoglycoside"})
        self.assertEqual(classes_of("water"), set())  # unmapped ≠ safe


class TestRenalVerdict(unittest.TestCase):
    def test_drug_and_class_resolution(self):
        # direct drug rule
        rv = renal_verdict("metformin", 25)
        self.assertEqual(rv["verdict"], "contraindicated")
        # class rule: ibuprofen resolves through nsaid
        rv = renal_verdict("ibuprofen", 40)
        self.assertEqual(rv["verdict"], "dose-adjust")
        self.assertIn("nsaid", rv["matched"])
        # inside all bands
        self.assertEqual(renal_verdict("metformin", 90)["verdict"],
                         "clear")
        # unrated is None — not clearance
        self.assertIsNone(renal_verdict("water", 20))
        self.assertEqual(rv["rule_version"], RENAL_RULES_VERSION)

    def test_most_restrictive_across_keys(self):
        # gentamicin has a drug rule; both drug and class are checked
        rv = renal_verdict("gentamicin", 20)
        self.assertEqual(rv["verdict"], "dose-adjust")
        self.assertIn("gentamicin", rv["matched"])


class TestCheckOrder(unittest.TestCase):
    def test_no_drug_refuses(self):
        with self.assertRaises(FormularyError):
            check_order("", allergies=())

    def test_allergy_is_contraindicated(self):
        out = check_order("ibuprofen", allergies=("NSAID",))
        kinds = {f["kind"]: f for f in out["findings"]}
        self.assertEqual(kinds["allergy"]["severity"], "contraindicated")
        self.assertFalse(out["clear"])

    def test_interaction_by_class(self):
        out = check_order("warfarin", current_meds=("diclofenac",))
        inter = [f for f in out["findings"] if f["kind"] == "interaction"]
        self.assertEqual(len(inter), 1)
        self.assertEqual(inter[0]["severity"], "major")
        self.assertEqual(inter[0]["against"], "diclofenac")
        self.assertIn("bleeding", inter[0]["mechanism"])
        self.assertTrue(inter[0]["evidence_ref"])  # no folklore rules

    def test_duplication_same_drug_and_same_class(self):
        out = check_order("ibuprofen", current_meds=("ibuprofen",))
        self.assertEqual(out["findings"][0]["severity"], "major")
        out = check_order("ibuprofen", current_meds=("naproxen",))
        dup = [f for f in out["findings"] if f["kind"] == "duplication"]
        self.assertEqual(dup[0]["severity"], "moderate")

    def test_dose_ceiling(self):
        out = check_order("paracetamol", dose_mg_per_day=5000)
        ceil = [f for f in out["findings"] if f["kind"] == "dose-ceiling"]
        self.assertEqual(len(ceil), 1)
        out = check_order("paracetamol", dose_mg_per_day=3000)
        self.assertTrue(out["clear"])

    def test_renal_finding_uses_versioned_table(self):
        out = check_order("metformin", egfr=25)
        ren = [f for f in out["findings"] if f["kind"] == "renal"]
        self.assertEqual(ren[0]["severity"], "contraindicated")
        self.assertEqual(ren[0]["rule_version"], "renal-rules@1.0.0")
        # unrated drug adds NO finding and stays honest in the note
        out = check_order("amoxicillin", egfr=10)
        self.assertTrue(out["clear"])
        self.assertIn("not clearance", out["note"])

    def test_compound_case_and_versions(self):
        out = check_order("spironolactone",
                          current_meds=("lisinopril",), egfr=25)
        sev = {f["severity"] for f in out["findings"]}
        self.assertIn("contraindicated", sev)   # renal
        self.assertIn("major", sev)             # hyperkalaemia pair
        self.assertEqual(out["formulary_version"], FORMULARY_VERSION)


if __name__ == "__main__":
    unittest.main()
