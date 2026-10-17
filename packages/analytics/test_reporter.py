"""Tests for the pilot-gates reporter — computed over a live HTTP fixture."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from reporter import report

# Fixture telemetry: a pilot going well enough to pass every gate.
EVENTS = (
    [{"type": "encounter", "ts": "2026-10-01T08:00:00Z",
      "captured_offline": True, "reconciled_at": "2026-10-01T08:05:00Z",
      "facility": "F-01"} for _ in range(95)]
    + [{"type": "encounter", "ts": "2026-10-01T09:00:00Z",
        "captured_offline": False, "reconciled_at": "2026-10-01T09:02:00Z",
        "facility": "F-01"} for _ in range(5)]
    + [{"type": "claim_submitted", "claim_id": f"C-{i}", "ts": "2026-10-01T10:00:00Z"}
       for i in range(10)]
    + [{"type": "claim_settled", "claim_id": f"C-{i}", "ts": "2026-10-05T10:00:00Z"}
       for i in range(10)]
    + [{"type": "stock_report", "facility": "F-01", "ts": f"2026-10-{d:02d}T07:00:00Z"}
       for d in range(1, 29)]
    + [{"type": "staff_activity", "staff_id": f"S-{i}", "ts": "2026-10-18T12:00:00Z"}
       for i in range(9)]
)
METRICS = {"events_received": len(EVENTS) + 12, "events_applied": len(EVENTS),
           "duplicates_dropped": 12}


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = METRICS if self.path == "/metrics" else EVENTS
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_):
        pass  # quiet


class TestReporter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FixtureHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_all_gates_pass_on_healthy_pilot(self):
        result = report(self.url, facilities=1, days=28, trained_staff=10)
        self.assertTrue(result["ready_to_scale"])
        self.assertEqual(result["gates_passed"], result["gates_total"])
        # Gate numbers are the real computations, not placeholders.
        self.assertTrue(result["gates"]["offline_continuity"]["pass"])
        self.assertEqual(result["gates"]["offline_continuity"]["offline_pct"], 95.0)
        self.assertTrue(result["gates"]["claims_turnaround"]["pass"])
        self.assertTrue(result["gates"]["stockout_visibility"]["pass"])
        self.assertEqual(result["gates"]["staff_weekly_active"]["active_pct"], 90.0)

    def test_ingest_health_carried_through(self):
        result = report(self.url, facilities=1, days=28, trained_staff=10)
        health = result["ingest_health"]
        self.assertEqual(health["duplicates_dropped"], 12)
        self.assertEqual(health["events_applied"], len(EVENTS))

    def test_failing_pilot_not_ready(self):
        # Fewer staff active than the gate requires.
        result = report(self.url, facilities=1, days=28, trained_staff=100)
        self.assertFalse(result["ready_to_scale"])
        self.assertFalse(result["gates"]["staff_weekly_active"]["pass"])


if __name__ == "__main__":
    unittest.main()
