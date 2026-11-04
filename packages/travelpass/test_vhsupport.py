"""Tests for vhsupport + vhmarts — helpdesk SLA clocks, multilingual
content with stated fallbacks, and actuarial marts with denominators."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhclaims import ClaimsRegistry  # noqa: E402
from vhcover import KeyRing, VHCCoverRegistry  # noqa: E402
from vhregistry import IncidentRegistry  # noqa: E402
from vhmarts import actuarial_mart  # noqa: E402
from vhsupport import (ContentLayer, Helpdesk,  # noqa: E402
                       SupportError)


class TestHelpdesk(unittest.TestCase):
    def test_full_lifecycle_within_sla(self):
        hd = Helpdesk(rules_version="vhc-support@1.0.0")
        t = hd.open_ticket(channel="whatsapp", category="claim",
                           priority="urgent",
                           subject="GOP not received at hospital",
                           cover_ref="VHC-1", now_h=100, day=1)
        hd.respond(t["ticket_id"], by="agent-1",
                   note="GOP re-issued to provider", now_h=100)
        hd.resolve(t["ticket_id"], by="agent-1",
                   resolution="provider confirmed receipt", now_h=105)
        hd.close(t["ticket_id"], by="agent-1", day=1)
        rep = hd.report()
        self.assertEqual(rep["first_response_sla"],
                         {"compliant": 1, "responded": 1})
        self.assertEqual(rep["by_state"]["closed"], 1)

    def test_late_response_is_a_named_breach(self):
        hd = Helpdesk(rules_version="vhc-support@1.0.0")
        t = hd.open_ticket(channel="email", category="quote",
                           priority="urgent", subject="x", now_h=0,
                           day=1)
        hd.respond(t["ticket_id"], by="a", note="n", now_h=3)
        self.assertEqual(t["breaches"][0]["clock"], "first_response")
        self.assertEqual(hd.report()["open_breaches"], 1)

    def test_closing_open_work_refuses(self):
        hd = Helpdesk(rules_version="vhc-support@1.0.0")
        t = hd.open_ticket(channel="phone", category="claim",
                           priority="standard", subject="x", now_h=0,
                           day=1)
        with self.assertRaises(SupportError):
            hd.close(t["ticket_id"], by="agent", day=1)


class TestContentLayer(unittest.TestCase):
    def test_stated_fallback(self):
        cl = ContentLayer()
        cl.publish("site@1.0.0", lang="en",
                   entries={"cover.title": "Visitor Health Cover"},
                   by="editor")
        out = cl.get("cover.title", lang="nl", version="site@1.0.0")
        self.assertTrue(out["fallback"])
        self.assertEqual(out["lang"], "en")
        self.assertIn("nl", out["note"])

    def test_missing_everywhere_is_a_content_gap(self):
        cl = ContentLayer()
        cl.publish("site@1.0.0", lang="fr", entries={"a": "b"},
                   by="editor")
        with self.assertRaises(SupportError):
            cl.get("missing.key", lang="en", version="site@1.0.0")

    def test_coverage_names_gaps_per_language(self):
        cl = ContentLayer()
        cl.publish("v1", lang="en", entries={"a": "1", "b": "2"},
                   by="ed")
        cl.publish("v1", lang="pt", entries={"a": "um"}, by="ed")
        cov = cl.coverage("v1")
        self.assertEqual(cov["languages"]["pt"]["missing"], ["b"])
        self.assertEqual(cov["languages"]["de"]["translated"], 0)


class TestActuarialMarts(unittest.TestCase):
    def test_ratios_with_denominators(self):
        ring = KeyRing()
        ring.register("k1", "s", active_from=0)
        vhc = VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                               key_ring=ring, issuer="cv")
        q = vhc.quote(days=30, age_years=35, day=1)
        vhc.purchase(quote_id=q["quote_id"], passport_ref="P",
                     full_name="N", entry_day=10, day=1, key_id="k1")
        claims = ClaimsRegistry(rules_version="vhc-claims@1.0.0",
                                tariff_set="cv-tariffs")
        case = claims.intake(channel="phone", cover_ref="VHC",
                             claimant_name="A", summary="s", now_h=0,
                             day=12)
        claims.issue_gop(case["case_id"], provider_ref="H",
                         limit_minor=99_000, now_h=1)
        claims.submit_dossier(case["case_id"], icd10_codes=["S01.0"],
                              acts=[{"code": "CONS-GP",
                                     "charged_minor": 3500}],
                              discharge_ref="D", day=13)
        claims.adjudicate(case["case_id"], day=14)
        claims.payment_run(day=15)
        inc = IncidentRegistry(
            rules_version="visitor-incidents@1.0.0", salt="s")
        inc.register_incident(traveler_key="T1",
                              syndromic_class="trauma", facility="H",
                              site="sal", outcome="discharged",
                              cost_minor=3500, day=12)
        mart = actuarial_mart(vhc=vhc, claims=claims, incidents=inc,
                              through_day=40)
        self.assertEqual(mart["premiums_minor"], 2500)
        self.assertEqual(mart["claims_paid_minor"], 3500)
        self.assertEqual(mart["loss_ratio"], 1.4)
        self.assertEqual(mart["covered_days"], 30)
        self.assertEqual(mart["frequency_per_1000_days"], 33.33)
        self.assertEqual(mart["severity_minor"], 3500)
        self.assertEqual(mart["incidents_by_site"]["sal"]
                         ["incidents"], 1)

    def test_no_premiums_withholds_ratios_but_shows_denominators(self):
        ring = KeyRing()
        ring.register("k1", "s", active_from=0)
        vhc = VHCCoverRegistry(profile_version="cv-vhc@1.0.0",
                               key_ring=ring, issuer="cv")
        mart = actuarial_mart(
            vhc=vhc,
            claims=ClaimsRegistry(rules_version="vhc-claims@1.0.0"),
            incidents=IncidentRegistry(
                rules_version="visitor-incidents@1.0.0", salt="s"),
            through_day=10)
        self.assertIsNone(mart["loss_ratio"])
        self.assertEqual(mart["premiums_minor"], 0)
        self.assertIn("withheld", mart["note"])


if __name__ == "__main__":
    unittest.main()
