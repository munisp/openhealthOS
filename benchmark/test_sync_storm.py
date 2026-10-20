import json, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from sync_storm import gen_events, storm

class Sink(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(202); self.end_headers(); self.wfile.write(b"{}")
    def log_message(self, *a): pass

def server():
    srv = HTTPServer(("127.0.0.1", 0), Sink)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv

def test_gen_events_unique():
    evs = gen_events("F1", 100)
    assert len({e["event_id"] for e in evs}) == 100

def test_storm_all_delivered():
    srv = server()
    out = storm(f"http://127.0.0.1:{srv.server_port}", facilities=20, events_per_facility=10)
    srv.shutdown()
    assert out["sent"] == 200 and out["failed"] == 0
    assert out["p95_ms"] >= out["p50_ms"]

def test_storm_fail_soft_when_down():
    out = storm("http://127.0.0.1:9", facilities=2, events_per_facility=3, workers=2)
    assert out["failed"] == 6 and out["sent"] == 0  # honestly tallied
