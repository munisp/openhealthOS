import unittest
from xrelease import ReleaseBook, XReleaseError


def _pending(book):
    return book.request_release(
        patient_id="p1", destination_country="GH",
        receiving_facility="korle-bu", ips_bundle_hash="sha256:abc",
        purpose="emergency-surgical-care")


class TestXRelease(unittest.TestCase):
    def setUp(self):
        self.b = ReleaseBook()

    def test_consent_then_transmit(self):
        r = _pending(self.b)
        self.b.grant_consent(r["id"], day=100, consented_by="Amina Yusuf",
                             method="signed")
        t = self.b.transmit(r["id"], day=100, bundle_hash="sha256:abc",
                            channel="qr-paper-bundle", transmitted_by="nurse-j")
        self.assertEqual(t["hash"], "sha256:abc")

    def test_no_consent_no_transmission(self):
        r = _pending(self.b)
        with self.assertRaises(XReleaseError):
            self.b.transmit(r["id"], day=100, bundle_hash="sha256:abc",
                            channel="x", transmitted_by="y")

    def test_hash_mismatch_refused_and_logged(self):
        r = _pending(self.b)
        self.b.grant_consent(r["id"], day=100, consented_by="p", method="signed")
        with self.assertRaises(XReleaseError):
            self.b.transmit(r["id"], day=100, bundle_hash="sha256:DIFFERENT",
                            channel="x", transmitted_by="y")
        events = [e["event"] for e in self.b.audit_trail(r["id"])]
        self.assertIn("transmission-refused-hash-mismatch", events)

    def test_emergency_override_double_locked(self):
        r = _pending(self.b)
        with self.assertRaises(XReleaseError):
            self.b.emergency_override(r["id"], day=100, senior_clinician="dr-a",
                                      administrator="dr-a",
                                      incapacity_reason="unconscious")
        with self.assertRaises(XReleaseError):
            self.b.emergency_override(r["id"], day=100, senior_clinician="dr-a",
                                      administrator="admin-b",
                                      incapacity_reason="")
        out = self.b.emergency_override(r["id"], day=100,
                                        senior_clinician="dr-a",
                                        administrator="admin-b",
                                        incapacity_reason="unconscious")
        self.assertEqual(out["consent"]["retrospective_review_due"], 103)

    def test_overdue_review_surfaced(self):
        r = _pending(self.b)
        self.b.emergency_override(r["id"], day=100, senior_clinician="dr-a",
                                  administrator="admin-b",
                                  incapacity_reason="unconscious")
        self.assertEqual(self.b.overrides_awaiting_review(as_of=102), [])
        od = self.b.overrides_awaiting_review(as_of=105)
        self.assertEqual(len(od), 1)
        self.assertEqual(od[0]["days_overdue"], 2)
        self.b.record_review(r["id"], day=105, reviewer="medical-director",
                             finding="override-justified")
        self.assertEqual(self.b.overrides_awaiting_review(as_of=105), [])

    def test_review_only_for_overrides(self):
        r = _pending(self.b)
        self.b.grant_consent(r["id"], day=100, consented_by="p", method="signed")
        with self.assertRaises(XReleaseError):
            self.b.record_review(r["id"], day=101, reviewer="x",
                                 finding="n/a")

    def test_revocation_honest_about_the_past(self):
        r = _pending(self.b)
        self.b.grant_consent(r["id"], day=100, consented_by="p", method="signed")
        self.b.transmit(r["id"], day=100, bundle_hash="sha256:abc",
                        channel="qr", transmitted_by="n")
        out = self.b.revoke(r["id"], day=101, by="p")
        self.assertEqual(out["already_transmitted"], 1)
        self.assertIn("cannot be un-sent", out["note"])
        with self.assertRaises(XReleaseError):
            self.b.transmit(r["id"], day=102, bundle_hash="sha256:abc",
                            channel="qr", transmitted_by="n")

    def test_incomplete_request_refused(self):
        with self.assertRaises(XReleaseError):
            self.b.request_release(patient_id="p1", destination_country="",
                                   receiving_facility="h",
                                   ips_bundle_hash="sha256:x", purpose="y")

    def test_audit_trail_append_only(self):
        r = _pending(self.b)
        self.b.grant_consent(r["id"], day=100, consented_by="p",
                             method="verbal-witnessed")
        trail = self.b.audit_trail(r["id"])
        self.assertEqual([e["event"] for e in trail], ["consent-granted"])


if __name__ == "__main__":
    unittest.main()
