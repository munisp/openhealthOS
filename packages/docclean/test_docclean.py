import unittest

import docclean as dc


class TestSimhash(unittest.TestCase):
    LETTER = ("Referral letter: Mrs A, 34, G3P2, presents with obstetric "
              "haemorrhage. Referring to general hospital for urgent "
              "management. Please receive.")

    def test_exact_duplicate_detected(self):
        idx = dc.DedupIndex()
        idx.check("DOC-1", self.LETTER)
        out = idx.check("DOC-2", self.LETTER)
        self.assertTrue(out["duplicate"])
        self.assertEqual(out["matches"][0][0], "DOC-1")

    def test_near_duplicate_detected(self):
        idx = dc.DedupIndex()
        idx.check("DOC-1", self.LETTER)
        rescanned = self.LETTER.replace("Please receive.", "Kindly receive.")
        out = idx.check("DOC-2", rescanned)
        self.assertTrue(out["duplicate"])

    def test_distinct_documents_not_flagged(self):
        idx = dc.DedupIndex()
        idx.check("DOC-1", self.LETTER)
        out = idx.check("DOC-2", "Discharge summary: Master B, 5, treated for "
                                 "uncomplicated malaria. Full recovery.")
        self.assertFalse(out["duplicate"])

    def test_empty_document_rejected(self):
        with self.assertRaises(dc.Error):
            dc.simhash("   !!! ")

    def test_hamming_symmetry(self):
        a, b = dc.simhash("hello world"), dc.simhash("hello there world")
        self.assertEqual(dc.hamming(a, b), dc.hamming(b, a))
        self.assertEqual(dc.hamming(a, a), 0)


class TestEnrichment(unittest.TestCase):
    def test_core_terms(self):
        self.assertEqual(dc.enrich_code("A00"), "Cholera")
        self.assertEqual(dc.enrich_code("A15.0"), "Respiratory tuberculosis")

    def test_unknown_codes_marked_not_guessed(self):
        self.assertEqual(dc.enrich_code("ZZ99"), "[uncoded: ZZ99]")

    def test_custom_dictionary_overrides(self):
        self.assertEqual(dc.enrich_code("X1", {"X1": "Local procedure"}),
                         "Local procedure")

    def test_resource_enrichment_adds_display(self):
        res = {"resourceType": "Condition",
               "code": {"coding": [{"system": "icd10", "code": "B05"}]}}
        out = dc.enrich_resource(res)
        self.assertEqual(out["code"]["coding"][0]["display"], "Measles")
        # input not mutated
        self.assertNotIn("display", res["code"]["coding"][0])

    def test_existing_display_preserved(self):
        res = {"code": {"coding": [{"code": "A00", "display": "Cholera (local name)"}]}}
        out = dc.enrich_resource(res)
        self.assertEqual(out["code"]["coding"][0]["display"], "Cholera (local name)")

    def test_nested_enrichment(self):
        res = {"resourceType": "Bundle",
               "entry": [{"resource": {"code": {"coding": [{"code": "B54"}]}}}]}
        out = dc.enrich_resource(res)
        self.assertEqual(out["entry"][0]["resource"]["code"]["coding"][0]["display"],
                         "Unspecified malaria")


if __name__ == "__main__":
    unittest.main()
