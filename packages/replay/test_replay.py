import json
import os
import tempfile
import unittest

from replay import load_adjudicator, replay, main

ADJ = os.path.join(os.path.dirname(__file__), "..", "adjudicator", "adjudicate.py")


def make_claim(cid="C-1", code="ANC-001", qty=1, price=None, member="NG-0001"):
    # Linked to the adjudicator's fixture encounter ENC-0003 (ADR-0018
    # linkage gate): same member, facility, and date — replayed claims
    # approve exactly as recorded.
    claim = {"claim_id": cid, "member_id": member, "provider_id": "PHC-007",
             "service_date": "2026-10-01", "encounter_id": "ENC-0003",
             "items": [{"code": code, "qty": qty}]}
    if price is not None:
        claim["items"][0]["unit_price"] = price
    return claim


class TestReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.adj = load_adjudicator(ADJ)

    def test_consistent_log_replays_clean(self):
        events = []
        for cid in ["C-1", "C-2", "C-3"]:
            claim = make_claim(cid)
            events.append({"type": "claim_received", "payload": claim})
            decision = self.adj.adjudicate(dict(claim))
            events.append({"type": "claim_decided", "payload": {**decision, "claim_id": cid}})
        replayed, divs = replay(events, self.adj)
        self.assertEqual(replayed, 3)
        self.assertEqual(divs, [])

    def test_tampered_decision_detected(self):
        claim = make_claim()
        events = [
            {"type": "claim_received", "payload": claim},
            # recorded decision contradicts what the rules produce
            {"type": "claim_decided", "payload": {"claim_id": "C-1", "decision": "DENY", "reasons": []}},
        ]
        replayed, divs = replay(events, self.adj)
        self.assertEqual(replayed, 1)
        self.assertEqual(len(divs), 1)
        self.assertEqual(divs[0].recorded, "DENY")
        self.assertEqual(divs[0].replayed, "APPROVE")

    def test_missing_decision_flagged(self):
        events = [{"type": "claim_received", "payload": make_claim()}]
        replayed, divs = replay(events, self.adj)
        self.assertEqual(replayed, 1)
        self.assertEqual(divs[0].recorded, "MISSING")

    def test_cli_exit_codes(self):
        claim = make_claim()
        good = [
            {"type": "claim_received", "payload": claim},
            {"type": "claim_decided", "payload": {**self.adj.adjudicate(dict(claim)), "claim_id": "C-1"}},
        ]
        with tempfile.TemporaryDirectory() as d:
            good_path = os.path.join(d, "good.jsonl")
            with open(good_path, "w") as fh:
                for ev in good:
                    fh.write(json.dumps(ev) + "\n")
            self.assertEqual(main(["--events", good_path, "--adjudicator", ADJ]), 0)

            bad_path = os.path.join(d, "bad.jsonl")
            with open(bad_path, "w") as fh:
                fh.write(json.dumps(good[0]) + "\n")
            report = os.path.join(d, "divs.json")
            self.assertEqual(main(["--events", bad_path, "--adjudicator", ADJ,
                                   "--report", report]), 1)
            with open(report) as fh:
                self.assertEqual(json.load(fh)[0]["recorded"], "MISSING")


if __name__ == "__main__":
    unittest.main()
