"""test_graphspike.py — pin the spike: reference engine must reproduce the
planted goldens exactly. These golden values are what the live AGE run
(run_age.sh --verify) must match before ADR-0064 can move to Accepted."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from synthdata import generate, to_csvs, SEED          # noqa: E402
from spike import RefGraph, run_spike, RING_MIN_CLAIMS  # noqa: E402


class TestSynthData(unittest.TestCase):
    def test_deterministic(self):
        a, b = generate(), generate()
        assert len(a["nodes"]) == len(b["nodes"]) and len(a["edges"]) == len(b["edges"])
        assert a["golden"] == b["golden"]

    def test_planted_ring_exists_in_data(self):
        d = generate()
        ghost_claims = [e for e in d["edges"] if e["type"] == "FILED_BY"
                        and e["dst"] == "OHF-XX-GHOST-9999"]
        assert len(ghost_claims) >= RING_MIN_CLAIMS

    def test_csvs_render(self):
        csvs = to_csvs(generate())
        assert "OHF-XX-GHOST-9999" in csvs["nodes"]
        assert "REFERRED_TO" in csvs["edges"]


class TestKillerQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = generate()
        cls.golden = cls.data["golden"]
        cls.result = run_spike(cls.data)
        cls.graph = RefGraph(cls.data["nodes"], cls.data["edges"])

    def test_fraud_ring_found_exactly(self):
        assert self.result["fraud_ring_facilities"] == self.golden["fraud_ring_facilities"]
        assert self.result["fraud_ring_prescribers"] == self.golden["fraud_ring_prescribers"]
        assert self.result["fraud_ring_accounts"] == self.golden["fraud_ring_accounts"]

    def test_honest_facilities_not_flagged(self):
        # no legitimate facility may trip the ring signature
        flagged = set(self.result["fraud_ring_facilities"])
        assert "OHF-FC-AMC-0001" not in flagged
        assert "OHF-FC-AMC-0101" not in flagged

    def test_contact_2hop_exactly_the_cluster(self):
        assert self.result["contact_2hop_from_PAT-0001"] == self.golden["contact_2hop_from_PAT-0001"]
        assert len(self.result["contact_2hop_from_PAT-0001"]) == 11
        assert "PAT-0001" not in self.result["contact_2hop_from_PAT-0001"]

    def test_orphan_and_top_referral(self):
        assert self.result["orphan_facilities"] == self.golden["orphan_facilities"]
        assert self.result["top_referral_into_tertiary"] == self.golden["top_referral_into_tertiary"]

    def test_referral_flow_counts(self):
        flow = self.graph.referral_flow()
        total = sum(f["count"] for f in flow["flows"])
        assert total > 0
        # every flow endpoint is a real facility
        for f in flow["flows"]:
            assert self.graph.labels[f["to"]] == "Facility"


class TestGoldenPinnedForLiveVerify(unittest.TestCase):
    def test_golden_file_written(self):
        out = subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "golden.py")],
                             capture_output=True, text=True)
        assert out.returncode == 0
        golden = json.loads(out.stdout)
        assert golden == self_golden()


def self_golden():
    return run_spike(generate(SEED))


if __name__ == "__main__":
    unittest.main()
