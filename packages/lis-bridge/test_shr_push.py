"""SHR push integration: results land in the record; outages never lose them."""
import json
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

from lis_bridge import LabOrder, LabQueue, SampleState
from shr import push_observation

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=timezone.utc)


class FakeSHR(BaseHTTPRequestHandler):
    received = []
    fail = False

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        type(self).received.append((self.path, self.headers.get("X-Facility-ID"), body))
        if type(self).fail:
            self.send_response(500)
        else:
            self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"stored"}')

    def log_message(self, *a):
        pass


class ShrFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FakeSHR.received = []
        FakeSHR.fail = False
        cls.httpd = HTTPServer(("127.0.0.1", 0), FakeSHR)
        cls.base = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()


def resulted_order() -> LabOrder:
    o = LabOrder(order_id="O-1", patient_id="PT-1", spoke_facility_id="PHC-1",
                 hub_lab_id="LAB-1", test="GLU")
    for st in (SampleState.COLLECTED, SampleState.IN_TRANSIT, SampleState.RECEIVED_AT_HUB):
        o.advance(st, NOW)
    return o


class TestPush(ShrFixture):
    def test_result_pushes_observation_with_provenance(self):
        q = LabQueue()
        q.place(resulted_order())
        obs = q.result("O-1", 5.4, NOW, shr_url=self.base)
        self.assertTrue(obs["shr_pushed"])
        path, facility_hdr, body = FakeSHR.received[-1]
        self.assertEqual(path, "/fhir/Observation/obs-O-1")
        self.assertEqual(facility_hdr, "PHC-1")
        self.assertEqual(body["patient_id"], "PT-1")
        self.assertEqual(body["source_facility"], "PHC-1")
        self.assertEqual(body["body"]["status"], "final")
        self.assertEqual(body["body"]["valueQuantity"]["value"], 5.4)

    def test_shr_outage_never_fails_the_result(self):
        FakeSHR.fail = True
        try:
            q = LabQueue()
            q.place(resulted_order())
            obs = q.result("O-1", 5.4, NOW, shr_url=self.base)
            self.assertEqual(obs["valueQuantity"]["value"], 5.4)  # result stands
            self.assertFalse(obs["shr_pushed"])                    # push honestly failed
            self.assertIn("shr_error", obs)
        finally:
            FakeSHR.fail = False

    def test_unreachable_shr_never_fails_the_result(self):
        q = LabQueue()
        q.place(resulted_order())
        obs = q.result("O-1", 5.4, NOW, shr_url="http://127.0.0.1:1")
        self.assertFalse(obs["shr_pushed"])

    def test_push_rejects_non_observation(self):
        with self.assertRaises(ValueError):
            push_observation(self.base, {"resourceType": "Patient"}, patient_id="P", facility_id="F")

    def test_no_shr_url_means_pure_local(self):
        q = LabQueue()
        q.place(resulted_order())
        obs = q.result("O-1", 4.2, NOW)
        self.assertNotIn("shr_pushed", obs)


if __name__ == "__main__":
    unittest.main()
