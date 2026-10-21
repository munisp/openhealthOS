"""test_bridge_fallback.py — silver->graph bridge and SQL fallback parity."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from lakefeed_bridge import bridge, read_silver_jsonl, write_csvs  # noqa: E402
from sql_fallback import QUERIES, parity_report                    # noqa: E402
from spike import run_spike                                        # noqa: E402

SILVER = [
    # ghost ring via silver events
    *[{"event_type": "claim_filed", "claim_id": f"C{i:03d}",
       "facility_id": "OHF-XX-GHOST-9999", "prescriber_id": "PRV-666",
       "amount": 30000, "paid_to": f"ACC-{13 + i % 2}"} for i in range(20)],
    # honest facility: few claims, many signers
    *[{"event_type": "claim_filed", "claim_id": f"H{i:03d}",
       "facility_id": "OHF-FC-AMC-0001", "prescriber_id": f"PRV-{i:03d}",
       "amount": 5000, "paid_to": f"ACC-{i % 10:02d}"} for i in range(5)],
    {"event_type": "encounter_held", "encounter_id": "E1",
     "facility_id": "OHF-FC-AMC-0001", "kind": "outreach",
     "attendees": ["PAT-1", "PAT-2", "PAT-3"]},
    {"event_type": "referral_made", "from_facility": "OHF-FC-AMC-0001",
     "to_facility": "OHF-FC-AMC-0101", "count": 7},
    {"event_type": "staff_posting", "prescriber_id": "PRV-001",
     "facility_id": "OHF-FC-AMC-0001"},
    {"event_type": "future_unseen_type", "note": "must be skipped honestly"},
]


class TestBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.graph = bridge(SILVER)

    def test_claim_edges(self):
        types = [(e["type"], e["dst"]) for e in self.graph["edges"] if e["src"] == "C000"]
        assert ("FILED_BY", "OHF-XX-GHOST-9999") in types
        assert ("SIGNED_BY", "PRV-666") in types
        assert ("PAID_TO", "ACC-13") in types

    def test_encounter_attendees(self):
        attendees = {e["src"] for e in self.graph["edges"]
                     if e["type"] == "ATTENDED" and e["dst"] == "E1"}
        assert attendees == {"PAT-1", "PAT-2", "PAT-3"}

    def test_unknown_event_skipped(self):
        # 20 ring claims + 5 honest claims + 1 encounter + 1 referral + 1 posting
        assert not any(n["label"] == "future_unseen_type" for n in self.graph["nodes"])

    def test_bridged_graph_reproduces_ring_verdict(self):
        result = run_spike(self.graph)
        assert result["fraud_ring_facilities"] == ["OHF-XX-GHOST-9999"]
        assert result["contact_2hop_from_PAT-0001"] == []  # no such patient here

    def test_jsonl_and_csv_io(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "part-0001.jsonl")
            with open(path, "w") as fh:
                for ev in SILVER[:3]:
                    fh.write(json.dumps(ev) + "\n")
                fh.write('{"broken json\n')  # flush-boundary partial line
            events = read_silver_jsonl(path)
            assert len(events) == 3  # partial line skipped, never fatal
            outdir = write_csvs(self.graph, os.path.join(d, "csv"))
            assert os.path.exists(os.path.join(outdir, "nodes.csv"))
            with open(os.path.join(outdir, "edges.csv")) as fh:
                assert "REFERRED_TO" in fh.read()


class TestFallbackParity(unittest.TestCase):
    def test_queries_present(self):
        assert set(QUERIES) == {"fraud_ring", "contact_2hop", "referral_flow"}

    def test_threshold_parity(self):
        rep = parity_report()
        assert rep["ring_min_claims"] == 15
        assert rep["sql_mentions_min_claims"]
        assert rep["sql_excludes_tertiary_orphans"]
        assert rep["sql_excludes_index_patient"]

    def test_fallback_sql_has_no_extension_calls(self):
        for q in QUERIES.values():
            assert "cypher(" not in q and "create_graph" not in q


if __name__ == "__main__":
    unittest.main()
