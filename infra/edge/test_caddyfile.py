"""Edge ingress config gates (ADR-0042/0053/0063): the Caddyfile is the
facility's front door — security directives are test-locked, not convention."""
import os, unittest

HERE = os.path.dirname(__file__)

def load():
    with open(os.path.join(HERE, "Caddyfile")) as fh:
        return fh.read()

class TestCaddyfile(unittest.TestCase):
    def test_security_headers(self):
        cfg = load()
        for h in ["Strict-Transport-Security", "X-Content-Type-Options",
                  "X-Frame-Options", "Content-Security-Policy"]:
            self.assertIn(h, cfg)

    def test_http3_enabled(self):
        self.assertIn("protocols h1 h2 h3", load())

    def test_tls_floor(self):
        self.assertIn("protocols tls1.2 tls1.3", load())

    def test_no_server_banner(self):
        self.assertIn("-Server", load())

    def test_api_never_cached(self):
        self.assertIn('header /api/* Cache-Control "no-store"', load())

    def test_body_cap(self):
        self.assertIn("max_size 1MB", load())

    def test_no_telemetry(self):
        self.assertIn("admin off", load())

if __name__ == "__main__":
    unittest.main()
