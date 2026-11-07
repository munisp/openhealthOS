import hashlib
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(__file__)
sys.path.insert(0, _HERE)
for _pkg in ("crosslink", "drugkb", "surveillance", "poe_screen", "steward",
             "medrec", "inpatient", "terminology", "mammoprog", "offlinespec",
             "teachfile", "formulary", "earlywarn", "cervixscreen", "usguard",
             "callrecall", "sepsis", "teleicu", "acuity", "telepath",
             "teleread", "reportkit", "pacs", "fertility", "rota", "triggers",
             "vitals", "mdt"):
    sys.path.insert(0, os.path.abspath(os.path.join(_HERE, "..", _pkg)))

from vhdash import (DASH_VERSION, br10_payload, export_dashboards,
                    fund_payload, helpdesk_payload)
from vhpilot import seed_pilot


class TestDashPayloads(unittest.TestCase):
    """Dashboards read the registries, not the narrative — and every
    payload carries the checksum it renders."""

    @classmethod
    def setUpClass(cls):
        cls.reg = seed_pilot(seed=42)["registries"]

    def _stamp_ok(self, payload):
        body = {k: v for k, v in payload.items() if k != "sha256"}
        expect = hashlib.sha256(json.dumps(body, sort_keys=True,
                                           separators=(",", ":"))
                                .encode()).hexdigest()
        self.assertEqual(payload["sha256"], expect)

    def test_fund_payload_five_buckets_with_denominators(self):
        p = fund_payload(self.reg["fund"])
        self.assertEqual(p["version"], DASH_VERSION)
        self.assertEqual(len(p["buckets"]), 5)
        total = sum(b["balance_minor"] for b in p["buckets"])
        self.assertEqual(total, p["allocated_minor"] - p["spent_minor"])
        self.assertGreater(p["allocated_minor"], 0)
        self.assertFalse(p["backstop"]["active"])
        self._stamp_ok(p)

    def test_fund_payload_currency_symbol_flows(self):
        p = fund_payload(self.reg["fund"], currency_symbol="CVE")
        self.assertEqual(p["currency_symbol"], "CVE")

    def test_br10_payload_unacknowledged_sorts_first(self):
        p = br10_payload(self.reg["vreg"])
        self.assertGreaterEqual(p["incidents"], 1)
        self._stamp_ok(p)
        # pilot seed 42: one alert, acknowledged — but the sort contract
        # is open-before-acked, verifiable by construction
        states = [a["acknowledged"] for a in p["alerts"]]
        self.assertEqual(states, sorted(states))
        acked = [a for a in p["alerts"] if a["acknowledged"]]
        for a in acked:
            self.assertIn("ack_by", a)
            self.assertIsNotNone(a["response_days"])

    def test_helpdesk_payload_sla_has_denominator(self):
        p = helpdesk_payload(self.reg["desk"])
        sla = p["first_response_sla"]
        self.assertGreaterEqual(sla["responded"], sla["compliant"])
        self.assertGreater(sla["responded"], 0)
        self.assertEqual(p["tickets_total"], len(p["tickets"]))
        self._stamp_ok(p)

    def test_helpdesk_breaches_named_not_hidden(self):
        p = helpdesk_payload(self.reg["desk"])
        open_breaches = sum(1 for t in p["tickets"]
                            if t["breaches"] and t["state"] != "closed")
        self.assertEqual(open_breaches, p["open_breaches"])

    def test_export_writes_three_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            paths = export_dashboards(self.reg, td)
            self.assertEqual(set(paths), {"vhc-fund.json",
                                          "vhc-br10.json",
                                          "vhc-helpdesk.json"})
            for path in paths.values():
                self.assertTrue(os.path.exists(path))
                payload = json.load(open(path))
                self.assertIn("sha256", payload)

    def test_payloads_deterministic_from_seed(self):
        again = seed_pilot(seed=42)["registries"]
        self.assertEqual(fund_payload(self.reg["fund"])["sha256"],
                         fund_payload(again["fund"])["sha256"])
        self.assertEqual(br10_payload(self.reg["vreg"])["sha256"],
                         br10_payload(again["vreg"])["sha256"])
        # helpdesk ticket IDs carry secrets.token_hex entropy — the
        # *content* is deterministic, the IDs are not; compare with
        # entropy-bearing fields normalized, because pretending
        # otherwise would be a flake farm
        def normalized(payload):
            body = {k: v for k, v in payload.items() if k != "sha256"}
            for t in body["tickets"]:
                t["ticket_id"] = "<id>"
            return body
        self.assertEqual(normalized(helpdesk_payload(self.reg["desk"])),
                         normalized(helpdesk_payload(again["desk"])))


class TestBoardFullyWired(unittest.TestCase):
    """With the three surfaces built, only proof stays unwired."""

    def test_all_scenes_but_proof_have_surfaces(self):
        from vhdemo import SCENE_SURFACES, build_board, build_walkthrough
        narrative = seed_pilot(seed=42)["narrative"]
        script = build_walkthrough(narrative)
        board = build_board(script, narrative)
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..", "apps", "pwa"))
        for s in board["scenes"]:
            if s["scene"] == "proof":
                self.assertIsNone(s["link"])
                continue
            self.assertIsNotNone(s["link"], f"{s['scene']} must be wired")
            self.assertTrue(os.path.exists(os.path.join(pwa, s["link"])),
                            f"{s['link']} must exist in apps/pwa")


class TestGuidedTour(unittest.TestCase):
    """The tour page walks the board in scene order. The page is
    structural — a contract the board JSON must satisfy — so the tests
    pin the contract, not the pixels."""

    @classmethod
    def setUpClass(cls):
        pwa = os.path.abspath(os.path.join(_HERE, "..", "..", "apps", "pwa"))
        cls.page = open(os.path.join(pwa, "vhc-tour.html")).read()
        narrative = seed_pilot(seed=42)["narrative"]
        from vhdemo import build_board, build_walkthrough
        cls.board = build_board(build_walkthrough(narrative), narrative)

    def test_tour_reads_the_board_json(self):
        self.assertIn("vhc-demo-board.json", self.page)

    def test_tour_walks_scenes_in_arc_order(self):
        # the page renders board.scenes sequentially with prev/next —
        # the board's scene order IS the tour order
        self.assertIn("board.scenes", self.page)
        self.assertIn("idx--", self.page)
        self.assertIn("idx++", self.page)
        order = [s["scene"] for s in self.board["scenes"]]
        self.assertEqual(order[0], "arrival")
        self.assertEqual(order[-1], "proof")

    def test_tour_shows_terminal_for_proof_not_a_link(self):
        # proof has no surface; the stage must render the command,
        # not a broken iframe
        self.assertIn("the proof is a command, not a slide", self.page)
        self.assertIn("bash demo/vhc-walkthrough.sh", self.page)
        proof = next(s for s in self.board["scenes"] if s["scene"] == "proof")
        self.assertIsNone(proof["link"])

    def test_tour_pins_red_findings(self):
        self.assertIn("STOP THE DEMO", self.page)
        self.assertIn("red_findings", self.page)

    def test_tour_iframes_only_linked_surfaces(self):
        # an iframe is created only when s.link is set — never for
        # an unwired scene
        self.assertIn('if (s.link)', self.page)
        self.assertIn('createElement("iframe")', self.page)


if __name__ == "__main__":
    unittest.main()
