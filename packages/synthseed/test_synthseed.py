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


class TestMedplumMode(unittest.TestCase):
    """Medplum targeting: /fhir/R4 base + client-credentials token fetch.
    Uses a stub HTTP server so the contract is tested, not assumed."""

    def test_medplum_load_flow(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        seen = {"token_calls": 0, "puts": [], "auth": []}

        class Stub(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path == "/oauth2/token":
                    seen["token_calls"] += 1
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    assert body["grant_type"] == "client_credentials"
                    assert body["client_id"] == "test-client"
                    out = json.dumps({"access_token": "tok-123"}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(out)
                else:
                    self.send_error(404)

            def do_PUT(self):
                seen["puts"].append(self.path)
                seen["auth"].append(self.headers.get("Authorization"))
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"

        d = tempfile.mkdtemp()
        profiles = os.path.join(d, "profiles.jsonl")
        with open(profiles, "w") as fh:
            fh.write(json.dumps(PROFILE) + "\n")
        out = os.path.join(d, "bundles")
        synthseed.convert(profiles, out, "PHC-DEMO", None)

        os.environ["MEDPLUM_CLIENT_ID"] = "test-client"
        os.environ["MEDPLUM_CLIENT_SECRET"] = "test-secret"
        try:
            n = synthseed.load(out, base, None, medplum=True)
        finally:
            srv.shutdown()
        self.assertEqual(seen["token_calls"], 1)
        self.assertEqual(n, len(seen["puts"]))
        self.assertTrue(all(p.startswith("/fhir/R4/") for p in seen["puts"]), seen["puts"])
        self.assertTrue(all(a == "Bearer tok-123" for a in seen["auth"]))
