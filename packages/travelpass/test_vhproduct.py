"""Data-product & channel tests — fraud rings, extracts, OTA widget,
seasonal pricing."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhfraudgraph import FraudGraph, FraudGraphError
from vhextract import (EXTRACT_CATALOG, ExtractDesk, ExtractError,
                       K_FLOOR, k_floor)
from vhwidget import WidgetError, WidgetRegistry
from vhsales import PartnerRegistry
from vhseason import (PreventionCampaigns, SeasonError, SeasonalTables)
from vhnotify import NotificationOutbox


class TestFraudGraph(unittest.TestCase):
    def _ring_graph(self):
        g = FraudGraph()
        for i, claimant in enumerate(("C1", "C2", "C3")):
            g.add_claim(claim_ref=f"CLM-{i}", claimant=claimant,
                        provider="clinic-x", payout_account="acct-99")
        return g

    def test_edges_refuse_hollow_claims(self):
        g = FraudGraph()
        with self.assertRaises(FraudGraphError):
            g.add_claim(claim_ref="c", claimant="a", provider="p",
                        payout_account="")

    def test_ring_detected_past_threshold(self):
        g = self._ring_graph()
        rings = g.detect_rings()
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0]["provider"], "clinic-x")
        self.assertEqual(len(rings[0]["claimants"]), 3)

    def test_honest_providers_do_not_ring(self):
        g = FraudGraph()
        for i in range(5):
            g.add_claim(claim_ref=f"CLM-{i}", claimant=f"C{i}",
                        provider=f"clinic-{i}",
                        payout_account=f"acct-{i}")
        self.assertEqual(g.detect_rings(), [])

    def test_evidence_pack_is_stamped(self):
        g = self._ring_graph()
        pack = g.evidence_pack(g.detect_rings()[0])
        self.assertEqual(len(pack["sha256"]), 64)
        self.assertEqual(len(pack["edges"]), 3)


class TestExtract(unittest.TestCase):
    def test_k_floor_suppresses_small_cells(self):
        kept, suppressed = k_floor([{"n": 10}, {"n": 4}, {"n": 5}])
        self.assertEqual(len(kept), 2)
        self.assertEqual(suppressed, 1)

    def test_unknown_extract_refuses(self):
        desk = ExtractDesk()
        with self.assertRaises(ExtractError):
            desk.buy("nope@9.9", buyer="reinsurer", day=0,
                     payment_ref="p")

    def test_fulfill_requires_license_and_stamps(self):
        desk = ExtractDesk()
        with self.assertRaises(ExtractError):
            desk.fulfill("ext_nope", rows=[])
        sale = desk.buy("corridor-loss-ratios@1.0.0",
                        buyer="reinsurer-ag", day=0, payment_ref="inv-1")
        pack = desk.fulfill(sale["token"],
                            rows=[{"n": 12, "corridor": "LOS-ABV"},
                                  {"n": 2, "corridor": "LOS-ENU"}])
        self.assertEqual(len(pack["rows"]), 1)     # small cell gone
        self.assertEqual(pack["suppressed_cells"], 1)
        self.assertEqual(pack["k_floor"], K_FLOOR)
        self.assertEqual(len(pack["sha256"]), 64)

    def test_gross_reported_by_currency(self):
        desk = ExtractDesk()
        desk.buy("corridor-loss-ratios@1.0.0", buyer="b", day=0,
                 payment_ref="p")
        rep = desk.report()
        self.assertEqual(rep["gross_minor_by_currency"]["EUR"], 250000)


class TestWidget(unittest.TestCase):
    def setUp(self):
        self.partners = PartnerRegistry()
        self.partners.register("ota-1", legal_name="BookFly",
                               api_key="k", day=0)
        self.w = WidgetRegistry(self.partners)
        self.token = self.w.issue_token("ota-1", "k",
                                        origin="https://bookfly.example",
                                        revenue_share_bps=1500, day=0)

    def test_origin_binding(self):
        with self.assertRaises(WidgetError):
            self.w.authenticate_token(self.token,
                                      origin="https://evil.example")
        rec = self.w.authenticate_token(self.token,
                                        origin="https://bookfly.example")
        self.assertEqual(rec["revenue_share_bps"], 1500)

    def test_tls_only_tokens(self):
        with self.assertRaises(WidgetError):
            self.w.issue_token("ota-1", "k", origin="http://x",
                               revenue_share_bps=100, day=0)

    def test_split_sums_to_gross(self):
        s = self.w.split_sale(self.token,
                              origin="https://bookfly.example",
                              gross_minor=931250)
        self.assertEqual(s["partner_minor"] + s["platform_minor"],
                         931250)
        self.assertEqual(s["partner_minor"], 139687)

    def test_share_band_enforced(self):
        with self.assertRaises(WidgetError):
            self.w.issue_token("ota-1", "k",
                               origin="https://bookfly.example",
                               revenue_share_bps=9000, day=0)


class TestSeasonal(unittest.TestCase):
    def test_table_is_four_eyes_and_forward_only(self):
        t = SeasonalTables()
        with self.assertRaises(SeasonError):
            t.ratify_table("ng-vhc@1.0.0", seasons=[], version=1,
                           ratified_by="a", approved_by="b", day=0)
        with self.assertRaises(SeasonError):
            t.ratify_table("ng-vhc@1.0.0",
                           seasons=[{"from_day": 0, "to_day": 90,
                                     "factor": 1.2}],
                           version=1, ratified_by="a", approved_by="a",
                           day=0)
        t.ratify_table("ng-vhc@1.0.0",
                       seasons=[{"from_day": 60, "to_day": 120,
                                 "factor": 1.2}],
                       version=1, ratified_by="a", approved_by="b", day=0)
        with self.assertRaises(SeasonError):
            t.ratify_table("ng-vhc@1.0.0",
                           seasons=[{"from_day": 0, "to_day": 30,
                                     "factor": 0.9}],
                           version=1, ratified_by="a", approved_by="b",
                           day=1)

    def test_effective_price_reads_the_table_in_force(self):
        t = SeasonalTables()
        t.ratify_table("ng-vhc@1.0.0",
                       seasons=[{"from_day": 60, "to_day": 120,
                                 "factor": 1.25}],
                       version=1, ratified_by="a", approved_by="b", day=0)
        self.assertEqual(t.effective_price("ng-vhc@1.0.0", 100000,
                                           day=90), 125000)
        self.assertEqual(t.effective_price("ng-vhc@1.0.0", 100000,
                                           day=10), 100000)
        self.assertEqual(t.effective_price("cv-vhc@1.0.0", 2500, day=90),
                         2500)          # no table: base price


class TestPreventionCampaigns(unittest.TestCase):
    def _outbox(self):
        sent = []

        def transport(channel, message):
            sent.append((channel, message))
            return {"status": "accepted"}
        return NotificationOutbox(transport,
                                  rules_version="vhc-notify@1.1.0"), sent

    def test_unacknowledged_alert_spends_nothing(self):
        outbox, _ = self._outbox()
        camp = PreventionCampaigns(outbox)
        with self.assertRaises(SeasonError):
            camp.trigger(alert={"ref": "BR10-1", "acknowledged": False},
                         channel="sms", lang="en", day=0,
                         funded_by="prevention-bucket")

    def test_acknowledged_alert_pushes_and_stamps(self):
        outbox, sent = self._outbox()
        camp = PreventionCampaigns(outbox)
        c = camp.trigger(alert={"ref": "BR10-1", "acknowledged": True},
                         channel="sms", lang="en", day=0,
                         funded_by="prevention-bucket")
        self.assertEqual(len(c["campaign_sha256"]), 64)
        self.assertEqual(len(sent), 1)


if __name__ == "__main__":
    unittest.main()
