import json
import os
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from auditlake import append, collect, verify, main


class AuditLakeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.dir.name, "audit.jsonl")

    def tearDown(self):
        self.dir.cleanup()

    def test_append_and_verify_roundtrip(self):
        append(self.log, "consent-service", "issue", "PT-1", {"scope": "treatment"})
        append(self.log, "shr-service", "access", "PT-1", {"facility": "F-9"})
        count, problems = verify(self.log)
        self.assertEqual(count, 2)
        self.assertEqual(problems, [])

    def test_tampered_entry_breaks_chain(self):
        append(self.log, "a", "issue", "PT-1")
        append(self.log, "b", "revoke", "PT-1")
        # attacker edits the first entry's subject
        lines = open(self.log).readlines()
        e = json.loads(lines[0])
        e["subject"] = "PT-999"
        lines[0] = json.dumps(e, sort_keys=True) + "\n"
        open(self.log, "w").writelines(lines)
        _, problems = verify(self.log)
        self.assertTrue(any("tampered" in p for p in problems))

    def test_deleted_middle_entry_detected(self):
        for i in range(4):
            append(self.log, "svc", f"action-{i}", f"S-{i}")
        lines = open(self.log).readlines()
        open(self.log, "w").writelines([lines[0]] + lines[2:])  # drop seq 2
        _, problems = verify(self.log)
        self.assertTrue(any("gap" in p or "broken" in p for p in problems))

    def test_cli_verify_exit_codes(self):
        append(self.log, "a", "x", "s")
        self.assertEqual(main(["verify", self.log]), 0)
        lines = open(self.log).readlines()
        e = json.loads(lines[0])
        e["action"] = "forged"
        open(self.log, "w").write(json.dumps(e, sort_keys=True) + "\n")
        self.assertEqual(main(["verify", self.log]), 1)

    def test_collect_dedups(self):
        events = [{"id": "ev-1", "actor": "consent-service", "action": "issue", "subject": "PT-1"}]

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(events).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_port}/audit"
        self.assertEqual(collect(self.log, [url]), 1)
        self.assertEqual(collect(self.log, [url]), 0)  # re-collect is a no-op
        count, problems = verify(self.log)
        self.assertEqual((count, problems), (1, []))
        srv.shutdown()


if __name__ == "__main__":
    unittest.main()
