#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "adapters"))
from flow import FlowError, SubmissionFlow  # noqa: E402
from nhia import NHIAAdapter  # noqa: E402

CLAIMS = [
    {"member_id": "NHIA-100001", "encounter_id": "E-1",
     "tariff_code": "CONS-01", "amount": 2500, "date": "2026-10-01"},
    {"member_id": "NHIA-100002", "encounter_id": "E-2",
     "tariff_code": "MAL-RDT", "amount": 1800, "date": "2026-10-02"},
    {"member_id": "NHIA-100003", "encounter_id": "E-3",
     "tariff_code": "CONS-01", "amount": 2500, "date": "2026-10-03"},
]


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.flow = SubmissionFlow(self.dir, NHIAAdapter(sandbox=True))

    def _staged(self):
        return self.flow.stage("NHIA-FFS", "OHF-KD-KDH-0001",
                               "2026-10", CLAIMS)

    def test_full_lifecycle_sandbox(self):
        led = self._staged()
        self.assertEqual(led["state"], "staged")
        self.assertTrue(led["conformant"])
        led = self.flow.submit(led["submission_hash"])
        self.assertEqual(led["state"], "submitted")
        self.assertTrue(all(c["reference"] for c in led["claims"]))
        remit = [{"reference": c["reference"], "outcome": "paid",
                  "amount_paid": c["amount"]} for c in led["claims"]]
        out = self.flow.reconcile(led["submission_hash"], remit)
        self.assertEqual(out["ledger"]["state"], "closed")
        self.assertEqual(out["report"]["amount_paid"], 6800)
        self.assertEqual(out["report"]["paid"], 3)

    def test_stage_is_idempotent(self):
        a = self._staged()
        b = self._staged()
        self.assertEqual(a["submission_hash"], b["submission_hash"])
        self.assertEqual(len(os.listdir(self.dir)), 2)  # json + sidecar

    def test_deterministic_claim_ids_survive_restart(self):
        led = self._staged()
        flow2 = SubmissionFlow(self.dir, NHIAAdapter(sandbox=True))
        led2 = flow2.load(led["submission_hash"])
        self.assertEqual([c["claim_id"] for c in led["claims"]],
                         [c["claim_id"] for c in led2["claims"]])

    def test_nonconformant_envelope_refused_at_submit(self):
        bad = CLAIMS + [{"member_id": "NHIA-100004"}]  # missing fields
        led = self.flow.stage("NHIA-FFS", "OHF-KD-KDH-0001", "2026-10", bad)
        self.assertFalse(led["conformant"])
        with self.assertRaises(FlowError) as ctx:
            self.flow.submit(led["submission_hash"])
        self.assertIn("not conformant", str(ctx.exception))
        # refused submissions stay staged — fixable, not lost
        self.assertEqual(self.flow.load(led["submission_hash"])["state"],
                         "staged")

    def test_reconcile_unknown_reference_refused(self):
        led = self._staged()
        self.flow.submit(led["submission_hash"])
        with self.assertRaises(FlowError) as ctx:
            self.flow.reconcile(led["submission_hash"], [
                {"reference": "NHIA-SBX-999999", "outcome": "paid",
                 "amount_paid": 5000}])
        self.assertIn("no such claim", str(ctx.exception))

    def test_partial_remittance_stays_reconciling(self):
        led = self._staged()
        h = led["submission_hash"]
        self.flow.submit(h)
        first = self.flow.load(h)["claims"][0]
        out = self.flow.reconcile(led["submission_hash"], [
            {"reference": first["reference"], "outcome": "queried",
             "reason": "tariff mismatch"}])
        self.assertEqual(out["ledger"]["state"], "reconciling")
        self.assertEqual(out["ledger"]["claims"][0]["reason"],
                         "tariff mismatch")

    def test_tampered_ledger_fails_loud(self):
        led = self._staged()
        self.flow.submit(led["submission_hash"])
        path = os.path.join(self.dir, led["submission_hash"] + ".json")
        doc = json.load(open(path))
        doc["claims"][0]["status"] = "paid"  # quiet fraud attempt
        json.dump(doc, open(path, "w"))
        with self.assertRaises(FlowError) as ctx:
            self.flow.load(led["submission_hash"])
        self.assertIn("integrity", str(ctx.exception))

    def test_duplicate_resubmit_returns_original_reference(self):
        led = self._staged()
        h = led["submission_hash"]
        self.flow.submit(h)
        refs1 = [c["reference"] for c in self.flow.load(h)["claims"]]
        # re-submit is a no-op for referenced claims; adapter dedupes anyway
        led2 = SubmissionFlow(self.dir, self.flow.adapter).submit(h)
        self.assertEqual(refs1,
                         [c["reference"] for c in led2["claims"]])


if __name__ == "__main__":
    unittest.main()
