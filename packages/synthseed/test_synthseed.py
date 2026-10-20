import json
import os
import tempfile
import unittest

import synthseed


PROFILE = {
    "patient_id": 1973,
    "demographics": {"age": 58, "gender": "Male"},
    "notes": [
        {"date": "2023-03-14", "note_type": "progress", "text": "58M with T2DM…",
         "diagnoses": [{"icd10": "E11.9", "display": "Type 2 diabetes mellitus"}]},
        {"date": "2023-06-01", "note_type": "inpatient_admission",
         "note": "Admitted with hyperglycaemia…",
         "diagnoses": [{"icd10": "E11.65"}, {"code": "I10", "name": "Hypertension"}]},
    ],
}


class TestConvert(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.profiles = os.path.join(self.dir, "profiles.jsonl")
        with open(self.profiles, "w") as fh:
            fh.write(json.dumps(PROFILE) + "\n")
        self.out = os.path.join(self.dir, "bundles")

    def load_bundle(self):
        synthseed.convert(self.profiles, self.out, "PHC-DEMO", None)
        name = os.listdir(self.out)[0]
        return json.load(open(os.path.join(self.out, name)))

    def test_bundle_shape(self):
        b = self.load_bundle()
        self.assertEqual(b["type"], "transaction")
        kinds = [e["resource"]["resourceType"] for e in b["entry"]]
        # 1 patient + 2 encounters + 2 docs + 3 conditions
        self.assertEqual(kinds.count("Patient"), 1)
        self.assertEqual(kinds.count("Encounter"), 2)
        self.assertEqual(kinds.count("DocumentReference"), 2)
        self.assertEqual(kinds.count("Condition"), 3)

    def test_provenance_tag_marks_synthetic(self):
        b = self.load_bundle()
        pat = [e["resource"] for e in b["entry"]
               if e["resource"]["resourceType"] == "Patient"][0]
        tags = [t["code"] for t in pat["meta"]["tag"]]
        self.assertIn("synthetic-hospital-v1.3", tags)

    def test_ids_are_deterministic(self):
        b1 = self.load_bundle()
        synthseed.convert(self.profiles, self.out, "PHC-DEMO", None)
        b2 = self.load_bundle()
        ids1 = [e["resource"]["id"] for e in b1["entry"]]
        ids2 = [e["resource"]["id"] for e in b2["entry"]]
        self.assertEqual(ids1, ids2)

    def test_put_upsert_requests(self):
        b = self.load_bundle()
        for e in b["entry"]:
            self.assertEqual(e["request"]["method"], "PUT")

    def test_field_aliases(self):
        # 'encounters' + 'code' alias + missing demographics must not crash.
        alt = {"id": "X1", "encounters": [
            {"encounter_date": "2024-01-01", "type": "ed", "content": "note",
             "conditions": [{"code": "A09"}]}]}
        with open(self.profiles, "w") as fh:
            fh.write(json.dumps(alt) + "\n")
        n = synthseed.convert(self.profiles, self.out, "PHC-DEMO", None)
        self.assertEqual(n, 1)
        b = self.load_bundle()
        kinds = [e["resource"]["resourceType"] for e in b["entry"]]
        self.assertEqual(kinds.count("Condition"), 1)

    def test_icd10_system_correct(self):
        b = self.load_bundle()
        cond = [e["resource"] for e in b["entry"]
                if e["resource"]["resourceType"] == "Condition"][0]
        self.assertEqual(cond["code"]["coding"][0]["system"],
                         "http://hl7.org/fhir/sid/icd-10-cm")

    def test_limit(self):
        with open(self.profiles, "a") as fh:
            fh.write(json.dumps({"patient_id": 9999}) + "\n")
        n = synthseed.convert(self.profiles, self.out, "PHC-DEMO", 1)
        self.assertEqual(n, 1)
        self.assertEqual(len(os.listdir(self.out)), 1)


if __name__ == "__main__":
    unittest.main()
