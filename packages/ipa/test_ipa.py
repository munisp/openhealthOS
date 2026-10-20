#!/usr/bin/env python3
import json
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from ipa import check_server, jwt_payload, main, scope_map


class FakeFHIR(BaseHTTPRequestHandler):
    """Minimal IPA-capable double: metadata + search + read."""
    resources = {
        "Patient": [{"id": "P1", "resourceType": "Patient"}],
        "Immunization": [{"id": "I1", "resourceType": "Immunization"}],
        "Observation": [{"id": "O1", "resourceType": "Observation"}],
    }

    def do_GET(self):
        parts = self.path.split("?")[0].strip("/").split("/")
        if self.path == "/metadata":
            body = {"resourceType": "CapabilityStatement",
                    "rest": [{"security": {"extension": [{"url": "smart"}]}}]}
            return self._send(200, body)
        if len(parts) == 1 and parts[0] in self.resources:
            entries = [{"resource": r} for r in self.resources[parts[0]]]
            return self._send(200, {"resourceType": "Bundle", "entry": entries})
        if len(parts) == 2 and parts[0] in self.resources:
            return self._send(200, self.resources[parts[0]][0])
        if len(parts) == 1:  # empty resource type — legal empty search
            return self._send(200, {"resourceType": "Bundle", "entry": []})
        self._send(404, {})

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FakeNoSMART(FakeFHIR):
    def do_GET(self):
        if self.path == "/metadata":
            return self._send(200, {"resourceType": "CapabilityStatement", "rest": []})
        super().do_GET()


class IPACase(unittest.TestCase):
    def _serve(self, handler):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def test_conformant_server(self):
        base = self._serve(FakeFHIR)
        report = check_server(base, sample_patient="P1")
        self.assertTrue(report["conformant"], report)
        self.assertEqual(report["failed"], 0)
        # empty resource types are skipped, not failed
        self.assertGreater(report["skipped"], 0)

    def test_missing_smart_flagged(self):
        base = self._serve(FakeNoSMART)
        report = check_server(base, sample_patient="P1")
        smart = next(r for r in report["results"]
                     if r["capability"] == "smart-security-advertised")
        self.assertFalse(smart["ok"])
        self.assertFalse(report["conformant"])

    def test_unreachable_fails_clean(self):
        report = check_server("http://127.0.0.1:1")
        self.assertFalse(report["conformant"])
        self.assertGreater(report["failed"], 0)

    def test_scope_map_covers_ipa_set(self):
        scopes = scope_map()
        for s in ("patient/Patient.read", "patient/Immunization.read",
                  "patient/Observation.read", "launch/patient", "offline_access"):
            self.assertIn(s, scopes)

    def test_jwt_payload(self):
        import base64 as b64
        payload = b64.urlsafe_b64encode(json.dumps({"scope": "patient/*.read"}).encode()).rstrip(b"=").decode()
        self.assertEqual(jwt_payload(f"x.{payload}.y")["scope"], "patient/*.read")
        self.assertEqual(jwt_payload("garbage"), {})

    def test_cli_scopes(self):
        self.assertEqual(main(["scopes"]), 0)

    def test_cli_check(self):
        base = self._serve(FakeFHIR)
        self.assertEqual(main(["check", "--base", base, "--patient", "P1"]), 0)


if __name__ == "__main__":
    unittest.main()
