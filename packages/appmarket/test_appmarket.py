"""App marketplace tests — declared scopes are the contract, installs
are four-eyes, patient data is consent-gated, revocation is instant."""

import unittest

from appmarket import (APPMARKET_VERSION, AppMarketError, AppRegistry,
                       SCOPES)


def _registry_with_app():
    r = AppRegistry()
    r.register_app("lab-results-plus", publisher="LabCo Sim",
                   version="1.2.0",
                   scopes=["system/ServiceRequest.read",
                           "system/DiagnosticReport.write"],
                   redirect_uris=["https://labco.example/cb"],
                   description="Lab results integration", day=0)
    return r


class TestPublish(unittest.TestCase):
    def test_undeclared_scope_refuses(self):
        r = AppRegistry()
        with self.assertRaises(AppMarketError):
            r.register_app("rogue", publisher="X", version="1",
                           scopes=["system/Everything.admin"],
                           redirect_uris=["https://x.example"],
                           description="rogue", day=0)

    def test_http_redirect_refuses(self):
        r = AppRegistry()
        with self.assertRaises(AppMarketError):
            r.register_app("plain", publisher="X", version="1",
                           scopes=["system/Coverage.read"],
                           redirect_uris=["http://x.example/cb"],
                           description="http", day=0)

    def test_duplicate_refuses(self):
        r = _registry_with_app()
        with self.assertRaises(AppMarketError):
            r.register_app("lab-results-plus", publisher="Y",
                           version="2", scopes=["system/Coverage.read"],
                           redirect_uris=["https://y.example"],
                           description="dup", day=1)

    def test_scope_catalogue_is_entries(self):
        self.assertIn("patient/IPS.read", SCOPES)
        self.assertIn("bulk/export.aggregate", SCOPES)


class TestInstall(unittest.TestCase):
    def test_four_eyes_install(self):
        r = _registry_with_app()
        with self.assertRaises(AppMarketError):
            r.install("lab-results-plus", facility="phc-sim-1",
                      by="admin-a", approved_by="admin-a", day=1)
        inst = r.install("lab-results-plus", facility="phc-sim-1",
                         by="admin-a", approved_by="admin-b", day=1)
        self.assertEqual(inst["state"], "active")

    def test_unknown_app_refuses(self):
        r = _registry_with_app()
        with self.assertRaises(AppMarketError):
            r.install("not-in-catalogue", facility="phc-sim-1",
                      by="a", approved_by="b", day=1)


class TestLaunchAndAccess(unittest.TestCase):
    def _active(self):
        r = _registry_with_app()
        r.install("lab-results-plus", facility="phc-sim-1",
                  by="admin-a", approved_by="admin-b", day=1)
        return r

    def test_launch_and_check(self):
        r = self._active()
        tok = r.launch("lab-results-plus", facility="phc-sim-1",
                       day=2, expires_day=32)
        ok = r.check_access(tok["token_id"],
                            scope="system/ServiceRequest.read", day=3)
        self.assertTrue(ok["allowed"])

    def test_undeclared_scope_refused_at_check(self):
        r = self._active()
        tok = r.launch("lab-results-plus", facility="phc-sim-1",
                       day=2, expires_day=32)
        with self.assertRaises(AppMarketError):
            r.check_access(tok["token_id"], scope="system/Claim.write",
                           day=3)

    def test_expired_token_refuses(self):
        r = self._active()
        tok = r.launch("lab-results-plus", facility="phc-sim-1",
                       day=2, expires_day=10)
        with self.assertRaises(AppMarketError):
            r.check_access(tok["token_id"],
                           scope="system/ServiceRequest.read", day=11)

    def test_patient_scope_needs_consent(self):
        r = AppRegistry()
        r.register_app("patient-viewer", publisher="P", version="1",
                       scopes=["patient/IPS.read"],
                       redirect_uris=["https://p.example"],
                       description="viewer", day=0)
        r.install("patient-viewer", facility="phc-sim-1",
                  by="a", approved_by="b", day=1)
        with self.assertRaises(AppMarketError):
            r.launch("patient-viewer", facility="phc-sim-1",
                     day=2, expires_day=32)          # no consent_ref
        tok = r.launch("patient-viewer", facility="phc-sim-1",
                       day=2, expires_day=32, consent_ref="consent-9")
        self.assertEqual(tok["consent_ref"], "consent-9")

    def test_revocation_kills_tokens(self):
        r = self._active()
        tok = r.launch("lab-results-plus", facility="phc-sim-1",
                       day=2, expires_day=32)
        r.revoke("lab-results-plus", facility="phc-sim-1", by="admin-b",
                 reason="publisher breached terms", day=3)
        with self.assertRaises(AppMarketError):
            r.check_access(tok["token_id"],
                           scope="system/ServiceRequest.read", day=3)

    def test_revoke_states_reason(self):
        r = self._active()
        with self.assertRaises(AppMarketError):
            r.revoke("lab-results-plus", facility="phc-sim-1",
                     by="admin-b", reason="", day=3)

    def test_report(self):
        r = self._active()
        rep = r.report()
        self.assertEqual(rep["version"], APPMARKET_VERSION)
        self.assertEqual(rep["apps"], 1)
        self.assertEqual(rep["installs_active"], 1)


if __name__ == "__main__":
    unittest.main()
