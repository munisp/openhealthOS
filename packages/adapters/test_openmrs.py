"""OpenMRS bridge tests — fake OpenMRS FHIR2 server + fake SHR, stdlib."""
from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from openmrs import OpenMRSBridge


class FakeServer(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # OpenMRS FHIR2 pull
        srv = self.server
        if "/ws/fhir2/R4/Patient" in self.path:
            srv.auth_seen.append(self.headers.get("Authorization"))
            self._send(200, {"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Patient", "id": "omrs-1",
                              "name": [{"family": "Bello"}]}},
                {"resource": {"resourceType": "Patient", "id": "omrs-2"}},
            ]})
        elif "/ws/fhir2/R4/Observation" in self.path:
            self._send(200, {"resourceType": "Bundle", "entry": [
                {"resource": {"resourceType": "Observation", "id": "obs-1",
                              "subject": {"reference": "Patient/omrs-1"}}},
            ]})
        else:
            self._send(404, {"error": "unknown"})

    def do_PUT(self):  # SHR envelope write
        srv = self.server
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        srv.shr_writes.append((self.path, self.headers.get("X-Facility-ID"), body))
        self._send(200, {"status": "ok"})

    def do_POST(self):  # OpenMRS create
        srv = self.server
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        srv.openmrs_creates.append((self.path, body))
        self._send(201, {"resourceType": body.get("resourceType"), "id": "new-1"})

    def log_message(self, *a):
        pass


def make_server():
    srv = HTTPServer(("127.0.0.1", 0), FakeServer)
    srv.auth_seen, srv.shr_writes, srv.openmrs_creates = [], [], []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class BridgeTest(unittest.TestCase):
    def test_pull_and_sync_into_shr(self):
        srv = make_server()
        base = f"http://127.0.0.1:{srv.server_port}"
        b = OpenMRSBridge(base, "admin", "Admin123", shr_url=base,
                          facility_id="HOSP-GEN-1", sandbox=False)
        res = b.sync("Patient")
        srv.shutdown(); srv.server_close()
        self.assertEqual(res.pulled, 2)
        self.assertEqual(res.pushed, 2)
        self.assertEqual(res.failed, 0)
        self.assertTrue(srv.auth_seen[0].startswith("Basic "))
        path, fac, env = srv.shr_writes[0]
        self.assertEqual(path, "/fhir/Patient/omrs-1")
        self.assertEqual(fac, "HOSP-GEN-1")
        self.assertEqual(env["source_facility"], "HOSP-GEN-1")
        self.assertEqual(env["body"]["meta"]["tag"][0]["code"], "openmrs")

    def test_patient_ref_from_observation(self):
        srv = make_server()
        base = f"http://127.0.0.1:{srv.server_port}"
        b = OpenMRSBridge(base, "admin", "x", shr_url=base,
                          facility_id="HOSP-GEN-1", sandbox=False)
        res = b.sync("Observation")
        srv.shutdown(); srv.server_close()
        self.assertEqual(res.pushed, 1)
        _, _, env = srv.shr_writes[0]
        self.assertEqual(env["patient_id"], "omrs-1")

    def test_push_referral_to_openmrs(self):
        srv = make_server()
        base = f"http://127.0.0.1:{srv.server_port}"
        b = OpenMRSBridge(base, "admin", "x", sandbox=False)
        out = b.push_openmrs({"resourceType": "ServiceRequest",
                              "status": "active", "intent": "order"})
        srv.shutdown(); srv.server_close()
        self.assertEqual(out["id"], "new-1")
        path, body = srv.openmrs_creates[0]
        self.assertEqual(path, "/ws/fhir2/R4/ServiceRequest")

    def test_sandbox_captures_without_network(self):
        b = OpenMRSBridge(sandbox=True, facility_id="F-1")
        b.push_shr_envelope({"resourceType": "Patient", "id": "p-1"})
        b.push_openmrs({"resourceType": "Patient", "id": "p-2"})
        self.assertEqual(len(b.captured_shr), 1)
        self.assertEqual(len(b.captured_openmrs), 1)
        self.assertEqual(b.captured_shr[0]["patient_id"], "p-1")

    def test_unsupported_pull_rejected(self):
        b = OpenMRSBridge(sandbox=True)
        res = b.sync("Procedure")
        self.assertEqual(res.pulled, 0)
        self.assertTrue(res.errors)

    def test_unreachable_shr_collected_not_raised(self):
        srv = make_server()
        base = f"http://127.0.0.1:{srv.server_port}"
        b = OpenMRSBridge(base, "a", "b", shr_url="http://127.0.0.1:1",
                          facility_id="F", sandbox=False)
        res = b.sync("Patient")
        srv.shutdown(); srv.server_close()
        self.assertEqual(res.pulled, 2)
        self.assertEqual(res.pushed, 0)
        self.assertEqual(res.failed, 2)


if __name__ == "__main__":
    unittest.main()
