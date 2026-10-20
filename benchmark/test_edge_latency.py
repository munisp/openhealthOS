#!/usr/bin/env python3
import http.server
import json
import os
import tempfile
import threading
import unittest

from edge_latency import LOSS_PROFILES, main, one_request, percentile, run, summarize, Sample
import urllib.parse


class SlowHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class BenchCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.port}/api/healthz"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_one_request_splits_phases(self):
        s = one_request(urllib.parse.urlparse(self.url), 5.0)
        self.assertTrue(s.ok)
        self.assertEqual(s.status, 200)
        self.assertGreater(s.total_ms, 0)
        self.assertGreaterEqual(s.ttfb_ms, s.connect_ms)
        self.assertGreater(s.bytes_in, 0)

    def test_unreachable_reports_error_never_raises(self):
        s = one_request(urllib.parse.urlparse("http://127.0.0.1:1/x"), 1.0)
        self.assertFalse(s.ok)
        self.assertTrue(s.error)

    def test_percentile(self):
        vals = list(range(1, 101))  # 1..100
        self.assertAlmostEqual(percentile(vals, 50), 50.5)
        self.assertEqual(percentile(vals, 100), 100)
        self.assertEqual(percentile([], 95), 0.0)

    def test_run_end_to_end(self):
        r = run(self.url, requests=40, concurrency=8, warmup=2, timeout=5.0)
        self.assertEqual(r["requests"], 40)
        self.assertEqual(r["ok"], 40)
        self.assertEqual(r["failed"], 0)
        self.assertGreater(r["rps"], 0)
        for key in ("p50", "p95", "p99", "mean"):
            self.assertIn(key, r["total_ms"])
            self.assertIn(key, r["connect_ms"])

    def test_summarize_counts_errors(self):
        samples = [
            Sample(True, 10, 2, 8, 200, 100),
            Sample(False, 5, 0, 0, 0, 0, "connection refused"),
            Sample(False, 5, 0, 0, 0, 0, "connection refused"),
        ]
        s = summarize(samples, 1.0)
        self.assertEqual(s["failed"], 2)
        self.assertEqual(s["errors"]["connection refused"], 2)

    def test_cli_json_out(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "r.json")
            rc = main(["--url", self.url, "--requests", "10",
                       "--concurrency", "4", "--warmup", "1", "--json", out])
            self.assertEqual(rc, 0)
            report = json.load(open(out))
            self.assertEqual(report["requests"], 10)

    def test_cli_loss_profile_prints_tc(self):
        for name in LOSS_PROFILES:
            rc = main(["--loss-profile", name])
            self.assertEqual(rc, 0)

    def test_cli_bad_url_fails(self):
        with self.assertRaises(ValueError):
            main(["--url", "ftp://nope"])


if __name__ == "__main__":
    unittest.main()
