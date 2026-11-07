"""Ops-hardening tests — PSP adapters, HSM seam, EASE rail, a11y,
metrics, abuse controls, offline PWA."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import vhpsp
from vhpsp import PSPError, parse_settlement_file, conformance
import vha11y
from vha11y import A11yError, assert_aa, contrast_ratio
import vhmetrics
from vhmetrics import MetricsError, evaluate_alerts, render_prometheus
from vhsales import PartnerRegistry, SalesError
from vhease import http_transport, EaseError
from vhcover import KeyRing, pkcs11_provider, VHCError


class TestPspAdapters(unittest.TestCase):
    CSV = (b"payer,amount_minor,currency,ref\n"
           b"Pax-0001,1200000,NGN,PSP-111\n"
           b"Pax-0002,550000,NGN,PSP-112\n")
    EXPECTED = [
        {"payer": "Pax-0001", "amount_minor": 1200000,
         "currency": "NGN", "ref": "PSP-111"},
        {"payer": "Pax-0002", "amount_minor": 550000,
         "currency": "NGN", "ref": "PSP-112"}]

    def test_csv_adapter_parses(self):
        self.assertEqual(parse_settlement_file(self.CSV,
                                               format_id="csv-v1"),
                         self.EXPECTED)

    def test_json_adapter_parses(self):
        raw = b'{"lines": [{"payer": "Pax-1", "amount_minor": 100, ' \
              b'"currency": "EUR", "ref": "r1"}]}'
        rows = parse_settlement_file(raw, format_id="json-v1")
        self.assertEqual(rows[0]["amount_minor"], 100)

    def test_unknown_format_refuses(self):
        with self.assertRaises(PSPError):
            parse_settlement_file(b"x", format_id="paystack-v9")

    def test_missing_columns_refuse(self):
        with self.assertRaises(PSPError):
            parse_settlement_file(b"payer,amount_minor\nPax,5\n",
                                  format_id="csv-v1")

    def test_conformance_pins_the_contract(self):
        self.assertTrue(conformance("csv-v1", self.CSV, self.EXPECTED))
        with self.assertRaises(PSPError):
            conformance("csv-v1", self.CSV, [])


class TestHsmSeam(unittest.TestCase):
    class FakePkcs11:
        def open_session(self, slot):
            return {"slot": slot}

        def sign(self, session, label, payload):
            return b"sig:" + payload

        def verify(self, session, label, payload, sig):
            return sig == b"sig:" + payload

    def test_absent_module_refuses(self):
        with self.assertRaises(VHCError):
            pkcs11_provider(None, slot=0, key_label="k")

    def test_module_missing_verbs_refuses(self):
        with self.assertRaises(VHCError):
            pkcs11_provider(object(), slot=0, key_label="k")

    def test_provider_pair_signs_and_verifies(self):
        signer, verifier = pkcs11_provider(self.FakePkcs11(), slot=0,
                                           key_label="k")
        sig = signer(b"payload")
        self.assertTrue(verifier(b"payload", sig))
        self.assertFalse(verifier(b"tampered", sig))

    def test_hsm_key_in_keyring_refuses_snapshot(self):
        kr = KeyRing()
        signer, verifier = pkcs11_provider(self.FakePkcs11(), slot=0,
                                           key_label="k")
        kr.register("hsm-1", signer=signer, verifier=verifier,
                    active_from=0, active_to=100)
        with self.assertRaises(VHCError):
            kr.snapshot()


class TestEaseRail(unittest.TestCase):
    def test_no_endpoint_refuses(self):
        with self.assertRaises(EaseError):
            http_transport("", token="t")

    def test_plain_http_refuses(self):
        with self.assertRaises(EaseError):
            http_transport("http://ease.example", token="t")

    def test_transport_posts_envelope(self):
        captured = {}

        def opener(url, body, headers):
            captured.update(url=url, body=body, headers=headers)
            return {"status": "accepted"}

        t = http_transport("https://ease.example/api", token="tok",
                           opener=opener)
        ack = t("cover_issued", {"type": "cover_issued", "payload": {},
                                 "rail_version": "ease@1.0",
                                 "idem_key": "x", "sent_at": 0})
        self.assertEqual(ack["status"], "accepted")
        self.assertEqual(captured["headers"]["Authorization"],
                         "Bearer tok")


class TestA11y(unittest.TestCase):
    def test_shipped_palette_passes_aa(self):
        rep = vha11y.report()
        self.assertEqual(rep["aa"], "pass")
        self.assertGreaterEqual(rep["worst_ratio"], 4.5)

    def test_bad_contrast_refuses(self):
        with self.assertRaises(A11yError):
            assert_aa("#777777", "#888888")

    def test_contrast_math_black_white(self):
        self.assertAlmostEqual(contrast_ratio("#000000", "#ffffff"),
                               21.0, places=1)

    def test_byte_budget(self):
        self.assertTrue(vha11y.page_bytes_ok(1000))
        with self.assertRaises(A11yError):
            vha11y.page_bytes_ok(200_000)


class TestMetrics(unittest.TestCase):
    def test_render_prometheus_vocabulary(self):
        text = render_prometheus({"vhc_settlement_ppm": 12})
        self.assertIn("vhc_settlement_ppm 12", text)
        with self.assertRaises(MetricsError):
            render_prometheus({"made_up_metric": 1})

    def test_alerts_fire_and_clear(self):
        firing = evaluate_alerts({
            "vhc_settlement_ppm": 1500, "vhc_helpdesk_sla_burn": 0.0,
            "vhc_br10_open_alerts": 0, "vhc_qr_verify_failures": 0,
            "vhc_fund_allocated_ratio": 1.0})
        self.assertEqual([f["rule"] for f in firing],
                         ["settlement_ppm_breach"])
        clean = evaluate_alerts({
            "vhc_settlement_ppm": 0, "vhc_helpdesk_sla_burn": 0.0,
            "vhc_br10_open_alerts": 0, "vhc_qr_verify_failures": 0,
            "vhc_fund_allocated_ratio": 1.0})
        self.assertEqual(clean, [])

    def test_missing_metric_value_refuses(self):
        with self.assertRaises(MetricsError):
            evaluate_alerts({})


class TestAbuseControls(unittest.TestCase):
    def setUp(self):
        self.reg = PartnerRegistry()
        self.reg.register("p1", legal_name="Acme", api_key="k", day=0,
                          quota_per_day=2, burst_per_minute=1)

    def test_burst_refuses_and_audits(self):
        self.reg.check_abuse_controls("p1", day=0, minute=10)
        with self.assertRaises(SalesError):
            self.reg.check_abuse_controls("p1", day=0, minute=10)
        self.assertTrue(any(a["event"] == "partner_burst_refused"
                            for a in self.reg.audit))

    def test_quota_refuses_next_day_resets(self):
        self.reg.check_abuse_controls("p1", day=0, minute=1)
        self.reg.check_abuse_controls("p1", day=0, minute=2)
        with self.assertRaises(SalesError):
            self.reg.check_abuse_controls("p1", day=0, minute=3)
        self.reg.check_abuse_controls("p1", day=1, minute=4)   # new day

    def test_unbounded_when_unset(self):
        r = PartnerRegistry()
        r.register("p2", legal_name="B", api_key="k", day=0)
        for i in range(50):
            r.check_abuse_controls("p2", day=0, minute=i)


class TestOfflinePwa(unittest.TestCase):
    def test_sw_registered_on_vhc_pages(self):
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..",
                                           "apps", "pwa"))
        for f in ("vhc-verify.html", "vhc-fund.html", "vhc-br10.html",
                  "vhc-helpdesk.html", "vhc-demo.html", "vhc-tour.html",
                  "traveler.html", "dispatch.html"):
            with open(os.path.join(pwa, f), encoding="utf-8") as fh:
                self.assertIn("sw-vhc.js", fh.read(), f)

    def test_sw_exists_and_precaches(self):
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..",
                                           "apps", "pwa"))
        with open(os.path.join(pwa, "sw-vhc.js"), encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn("vhc-shell-v1", body)
        self.assertIn("stale-cache", body)


if __name__ == "__main__":
    unittest.main()
