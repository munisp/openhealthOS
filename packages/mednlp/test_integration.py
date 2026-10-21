"""test_integration.py — ADR-0065 integration: silver free-text gate and
the release-time model gate."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lakehouse"))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools", "release"))

import lakehouse                      # noqa: E402
from silver_hook import make_scrubber  # noqa: E402
import sbom                            # noqa: E402

NOTE_EVENT = {
    "id": "n1", "ts": "2026-10-01T09:00:00", "facility": "OHF-FC-AMC-0001",
    "type": "clinical_note",
    "free_text": ["note"],
    "payload": {"registry": "hiv",
                "note": "NIN 12345678901 called from 08035551234: viral load due"},
}


class TestSilverFreeTextGate(unittest.TestCase):
    def test_free_text_without_scrubber_refused(self):
        with tempfile.TemporaryDirectory() as d:
            try:
                lakehouse.silver([NOTE_EVENT], d)
                raise AssertionError("must refuse")
            except lakehouse.FreeTextGateError as e:
                assert "free_text" in str(e)

    def test_free_text_scrubbed_on_entry(self):
        with tempfile.TemporaryDirectory() as d:
            n = lakehouse.silver([NOTE_EVENT], d, scrub=make_scrubber())
            assert n == 1
            path = os.path.join(d, "silver", "type=clinical_note", "records.jsonl")
            rows = [json.loads(l) for l in open(path)]
            assert "12345678901" not in rows[0]["note"]
            assert "08035551234" not in rows[0]["note"]
            assert "[NIN]" in rows[0]["note"]
            assert "viral load due" in rows[0]["note"]  # clinical content survives

    def test_structured_events_unaffected(self):
        ev = {"id": "e1", "ts": "2026-10-01T00:00:00", "facility": "F",
              "type": "registry_enrolment",
              "payload": {"registry": "hiv", "stage": 1}}
        with tempfile.TemporaryDirectory() as d:
            assert lakehouse.silver([ev], d) == 1  # no scrubber needed


class TestReleaseModelGate(unittest.TestCase):
    def test_repo_manifest_fails_closed_until_live_verify(self):
        root = os.path.join(HERE, "..", "..")
        failures = sbom.model_gate(os.path.abspath(root))
        # shipped manifest carries PENDING-LIVE-VERIFY pins: the gate MUST
        # report them (this assertion flips to empty after live verify)
        assert failures, "placeholder pins must not pass the release gate"
        assert "models-manifest.json" in failures[0]

    def test_no_manifest_is_clean(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "packages", "mednlp"))
            import shutil
            shutil.copy(os.path.join(HERE, "model_gate.py"),
                        os.path.join(d, "packages", "mednlp", "model_gate.py"))
            assert sbom.model_gate(d) == []


if __name__ == "__main__":
    unittest.main()
