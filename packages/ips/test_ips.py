import unittest
from ips import build_ips, fingerprint, validate_ips, IPSError

PATIENT = {"id": "p1", "name": "Amina Yusuf", "birthDate": "1994-03-01",
           "gender": "female"}


def _sections():
    return {
        "medications": {"entries": [
            {"resourceType": "MedicationStatement",
             "medication": "artemether-lumefantrine"}]},
        "allergies": {"empty_reason": "none-known"},
        "problems": {"entries": [
            {"resourceType": "Condition", "code": "ruptured-ectopic"}]},
        "immunizations": {"entries": [
            {"resourceType": "Immunization", "vaccineCode": "Yellow-fever"}]},
    }


class TestIPS(unittest.TestCase):
    def setUp(self):
        self.bundle = build_ips(PATIENT, sections=_sections(),
                                author="openhealth-ng/phc-1",
                                generated_day=100)

    def test_document_bundle_shape(self):
        self.assertEqual(self.bundle["resourceType"], "Bundle")
        self.assertEqual(self.bundle["type"], "document")
        comp = self.bundle["entry"][0]["resource"]
        self.assertEqual(comp["resourceType"], "Composition")
        self.assertEqual(comp["type"]["coding"][0]["code"], "60591-5")

    def test_required_sections_missing_refused(self):
        with self.assertRaises(IPSError) as cm:
            build_ips(PATIENT, sections={"medications": {"entries": []}},
                      author="x", generated_day=1)
        self.assertIn("allergies", str(cm.exception))

    def test_empty_reason_validated(self):
        s = _sections()
        s["allergies"] = {"empty_reason": "classified"}
        with self.assertRaises(IPSError):
            build_ips(PATIENT, sections=s, author="x", generated_day=1)

    def test_withheld_is_honest(self):
        s = _sections()
        s["problems"] = {"empty_reason": "withheld-by-consent"}
        b = build_ips(PATIENT, sections=s, author="x", generated_day=1)
        comp = b["entry"][0]["resource"]
        prob = next(sec for sec in comp["section"]
                    if sec["title"] == "Problem List")
        self.assertEqual(prob["emptyReason"]["coding"][0]["code"],
                         "withheld-by-consent")

    def test_unknown_section_and_anonymous_patient_refused(self):
        with self.assertRaises(IPSError):
            build_ips(PATIENT, sections={**_sections(), "gossip": {}},
                      author="x", generated_day=1)
        with self.assertRaises(IPSError):
            build_ips({"id": "", "name": ""}, sections=_sections(),
                      author="x", generated_day=1)

    def test_entries_referenced(self):
        comp = self.bundle["entry"][0]["resource"]
        med = next(s for s in comp["section"] if s["title"].startswith("History of Medication"))
        self.assertEqual(len(med["entry"]), 1)
        resources = [e["resource"] for e in self.bundle["entry"]]
        self.assertTrue(any(r.get("resourceType") == "MedicationStatement"
                            for r in resources))
        self.assertTrue(any(r.get("resourceType") == "Immunization"
                            for r in resources))

    def test_fingerprint_stable_and_sensitive(self):
        f1 = fingerprint(self.bundle)
        self.assertTrue(f1.startswith("sha256:"))
        self.assertEqual(f1, fingerprint(self.bundle))
        other = build_ips(PATIENT, sections=_sections(), author="x",
                          generated_day=101)
        self.assertNotEqual(f1, fingerprint(other))

    def test_validate_ips(self):
        self.assertTrue(validate_ips(self.bundle)["valid"])
        out = validate_ips({"resourceType": "Bundle", "type": "collection"})
        self.assertFalse(out["valid"])


if __name__ == "__main__":
    unittest.main()
