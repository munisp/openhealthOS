"""Config-gate tests for the APISIX declarative config (ADR-0062): the route
table must stay in lockstep with the SDK surface and the security doctrine
(every route authenticated, payer routes rate-limited)."""
import os, re, unittest

HERE = os.path.dirname(__file__)

def load():
    with open(os.path.join(HERE, "apisix.yaml")) as fh:
        return fh.read()

class TestApisixConfig(unittest.TestCase):
    def test_every_sdk_path_has_a_route(self):
        cfg = load()
        for path in ["/api/claims/", "/api/fhir/", "/api/cds/", "/api/terminology/"]:
            self.assertIn(path, cfg, f"missing route for {path}")

    def test_every_route_authenticated(self):
        cfg = load()
        routes = re.split(chr(10)+"  - uri: ", cfg)[1:]
        self.assertGreaterEqual(len(routes), 5)
        for r in routes:
            uri = r.split(chr(10))[0]
            self.assertIn("openid-connect", r, f"route {uri} lacks authn")

    def test_no_etcd_dependency(self):
        self.assertIn("standalone", load().lower())

    def test_no_committed_secrets(self):
        cfg = load()
        # secrets must be env placeholders, never literals
        self.assertNotRegex(cfg, r"client_secret: [^{$\s]")
        self.assertNotRegex(cfg, r"key: (?!consumer_name|consumer)[^{$\s]")

    def test_rate_limits_present_on_mutating_surfaces(self):
        cfg = load()
        claims = [r for r in re.split(chr(10)+"  - uri: ", cfg) if r.startswith("/api/claims/")][0]
        self.assertIn("limit-count", claims)
        bulk = [r for r in re.split(chr(10)+"  - uri: ", cfg) if r.startswith("/api/fhir/$export")][0]
        self.assertIn("limit-count", bulk)

    def test_request_id_echoed(self):
        self.assertGreaterEqual(load().count("request-id"), 3)

if __name__ == "__main__":
    unittest.main()
