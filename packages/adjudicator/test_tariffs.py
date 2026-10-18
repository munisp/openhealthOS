#!/usr/bin/env python3
"""Tariff bundles: validation is total, state uploads take effect live,
decisions stamp the bundle that priced them (ADR-0015)."""
import json
import os
import tempfile
import unittest
from datetime import date

import tariffs
from adjudicate import adjudicate, preauth


def valid_bundle(**over):
    b = {
        "bundle_id": "kd-2026", "version": 2, "state": "KD",
        "effective_from": "2026-01-01",
        "codes": {"MAL-RDT": {"amount": 2000, "name": "Malaria RDT"},
                  "ANC-001": {"amount": 4500, "name": "ANC visit"},
                  "LAB-MP": {"amount": 4000, "name": "Malaria microscopy"}},
        "freq_caps": {"MAL-RDT": 3},
    }
    b.update(over)
    return b


CLAIM = {"claim_id": "C-1", "member_id": "NG-0001", "provider_id": "F-1",
         "service_date": "2026-10-01", "claimed_ngn": 0,
         "items": [{"code": "MAL-RDT", "qty": 1}]}


class TestValidation(unittest.TestCase):
    def test_valid_bundle_passes(self):
        self.assertEqual(tariffs.validate_bundle(valid_bundle()), [])

    def test_missing_keys_reported_together(self):
        errs = tariffs.validate_bundle({})
        self.assertTrue(any("bundle_id" in e for e in errs))
        self.assertTrue(any("codes" in e for e in errs))
        self.assertGreaterEqual(len(errs), 4)

    def test_bad_amounts_and_caps(self):
        b = valid_bundle()
        b["codes"]["X"] = {"amount": -5, "name": "bad"}
        b["codes"]["Y"] = {"amount": 100}  # no name
        b["freq_caps"]["GHOST"] = 2        # not a tariffed code
        errs = tariffs.validate_bundle(b)
        self.assertTrue(any("X.amount" in e for e in errs))
        self.assertTrue(any("Y.name" in e for e in errs))
        self.assertTrue(any("freq_caps.GHOST" in e for e in errs))

    def test_future_effective_date_rejected(self):
        errs = tariffs.validate_bundle(valid_bundle(effective_from="2027-01-01"),
                                       today=date(2026, 6, 1))
        self.assertTrue(any("future" in e for e in errs))

    def test_load_bundle_raises_on_garbage(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
            path = f.name
        try:
            with self.assertRaises(tariffs.BundleError):
                tariffs.load_bundle(path)
        finally:
            os.unlink(path)


class TestLiveBundleSwitch(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("OH_TARIFF_BUNDLE")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("OH_TARIFF_BUNDLE", None)
        else:
            os.environ["OH_TARIFF_BUNDLE"] = self._old

    def _write(self, b):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(b, f)
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_state_upload_reprices_without_redeploy(self):
        path = self._write(valid_bundle())
        os.environ["OH_TARIFF_BUNDLE"] = path
        d = adjudicate(dict(CLAIM))
        self.assertEqual(d["allowed_ngn"], 2000)          # state price, not 1500
        self.assertEqual(d["tariff_bundle"], "kd-2026@v2")  # stamped
        p = preauth({"member_id": "NG-0001", "provider_id": "F-1",
                     "items": [{"code": "MAL-RDT", "qty": 1}]})
        self.assertEqual(p["estimated_ngn"], 2000)
        self.assertEqual(p["tariff_bundle"], "kd-2026@v2")

    def test_default_bundle_when_unset(self):
        os.environ.pop("OH_TARIFF_BUNDLE", None)
        d = adjudicate(dict(CLAIM))
        self.assertEqual(d["allowed_ngn"], 1500)
        self.assertEqual(d["tariff_bundle"], "ng-default-2026@v1")

    def test_missing_override_falls_back_not_crashes(self):
        os.environ["OH_TARIFF_BUNDLE"] = "/nonexistent/bundle.json"
        d = adjudicate(dict(CLAIM))
        self.assertEqual(d["allowed_ngn"], 1500)  # import-time bundle
        self.assertIn("tariff_bundle", d)


if __name__ == "__main__":
    unittest.main()
