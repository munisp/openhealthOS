import json
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from dhis2 import DHIS2Adapter
from httpclient import AdapterError, HTTPClient
from nhia import NHIAAdapter
from nimc import NIMCAdapter
from psp import PSPAdapter


class FakeUpstream(BaseHTTPRequestHandler):
    """One handler serves all adapter HTTP tests; routes by path."""
    posts = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeUpstream.posts.append((self.path, body))
        out = {}
        if self.path == "/verify":
            out = {"match": body.get("nin") == "12345678901", "name": "Test Person"}
        elif self.path == "/claims":
            out = {"reference": "NHIA-LIVE-1", "status": "received"}
        elif self.path in ("/collect", "/payout"):
            out = {"ref": "PSP-LIVE-1", "status": "success"}
        elif self.path == "/api/dataValueSets":
            out = {"status": "SUCCESS"}
        raw = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        raw = json.dumps({"status": "paid"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


class LiveCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), FakeUpstream)
        Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
        FakeUpstream.posts.clear()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()


class TestNIMC(LiveCase):
    def test_sandbox_verify_and_mismatch(self):
        a = NIMCAdapter(sandbox=True)
        a.register_fixture("12345678901", "Amina Yusuf", "1990-04-02")
        self.assertEqual(a.verify("12345678901", "1990-04-02").status, "verified")
        self.assertEqual(a.verify("12345678901", "1991-01-01").status, "no_match")
        self.assertEqual(a.verify("99999999999").status, "deferred")

    def test_format_gate(self):
        a = NIMCAdapter(sandbox=True)
        self.assertEqual(a.verify("123").status, "invalid_format")
        self.assertEqual(a.verify("abcdefghijkl").status, "invalid_format")

    def test_live_verify(self):
        a = NIMCAdapter(base_url=self.base, token="t", sandbox=False)
        self.assertEqual(a.verify("12345678901").status, "verified")
        self.assertEqual(a.verify("10987654321").status, "no_match")


class TestNHIA(LiveCase):
    CLAIM = {"claim_id": "C-9", "member_id": "NG-0001", "service_date": "2026-01-15"}

    def test_sandbox_submit_and_dedup(self):
        a = NHIAAdapter(sandbox=True)
        ok = a.submit_claim(self.CLAIM, {"decision": "APPROVE"})
        self.assertEqual(ok.status, "received")
        dup = a.submit_claim(self.CLAIM, {"decision": "APPROVE"})
        self.assertEqual(dup.status, "duplicate")
        self.assertEqual(dup.reference, ok.reference)
        self.assertEqual(a.status("C-9"), "received")

    def test_only_approved_submitted(self):
        a = NHIAAdapter(sandbox=True)
        r = a.submit_claim(self.CLAIM, {"decision": "PEND"})
        self.assertEqual(r.status, "error")
        self.assertEqual(len(a.submitted()), 0)

    def test_live_submit(self):
        a = NHIAAdapter(base_url=self.base, sandbox=False)
        r = a.submit_claim(self.CLAIM, {"decision": "APPROVE"})
        self.assertEqual(r.reference, "NHIA-LIVE-1")


class TestPSP(LiveCase):
    def test_instalment_band_enforced(self):
        a = PSPAdapter(sandbox=True)
        self.assertEqual(a.collect("W-1", 100, "n1").status, "rejected")
        self.assertEqual(a.collect("W-1", 5000, "n2").status, "rejected")
        self.assertEqual(a.collect("W-1", 1000, "n3").status, "success")

    def test_idempotent_retry(self):
        a = PSPAdapter(sandbox=True)
        r1 = a.payout("acct-1", 50000, "settle-C-9")
        r2 = a.payout("acct-1", 50000, "settle-C-9")  # network retry
        self.assertEqual(r2.status, "duplicate")
        self.assertEqual(r2.ref, r1.ref)
        self.assertEqual(len(a.operations()), 1)

    def test_live_collect(self):
        a = PSPAdapter(base_url=self.base, provider="paystack", sandbox=False)
        r = a.collect("W-9", 1500, "n9")
        self.assertEqual(r.status, "success")


class TestDHIS2(LiveCase):
    PAYLOAD = {"dataSet": "OH-WEEKLY", "period": "2026W42", "orgUnit": "LGA-01",
               "dataValues": [{"dataElement": "ENCOUNTERS", "value": 412}]}

    def test_sandbox_captures(self):
        a = DHIS2Adapter(sandbox=True)
        r = a.push_data_value_set(self.PAYLOAD)
        self.assertEqual((r.status, r.values), ("captured", 1))
        self.assertEqual(len(a.captured), 1)

    def test_patient_level_payload_refused(self):
        a = DHIS2Adapter(sandbox=True)
        bad = dict(self.PAYLOAD)
        bad["dataValues"] = [{"dataElement": "X", "value": 1, "patient_id": "P-1"}]
        r = a.push_data_value_set(bad)
        self.assertEqual(r.status, "refused")
        self.assertEqual(len(a.captured), 0)

    def test_empty_refused(self):
        a = DHIS2Adapter(sandbox=True)
        self.assertEqual(a.push_data_value_set({"dataValues": []}).status, "refused")

    def test_live_push(self):
        a = DHIS2Adapter(base_url=self.base, sandbox=False)
        self.assertEqual(a.push_data_value_set(self.PAYLOAD).status, "pushed")


class TestHTTPClient(LiveCase):
    def test_unreachable_raises_adapter_error(self):
        c = HTTPClient("http://127.0.0.1:1", timeout=0.2, retries=1, backoff=0.01)
        with self.assertRaises(AdapterError):
            c.request("GET", "/x")


if __name__ == "__main__":
    unittest.main()
