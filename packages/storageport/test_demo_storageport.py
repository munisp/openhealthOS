"""storageproof demo tests — determinism, stamp contract, payload
shape, and drills that are real."""

import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "payloadio"))

import demo_storageport  # noqa: E402
import storageport  # noqa: E402


class TestDeterminism(unittest.TestCase):
    def test_two_runs_same_bytes(self):
        a = demo_storageport.run_demo()
        b = demo_storageport.run_demo()
        self.assertEqual(
            json.dumps(a, sort_keys=True, separators=(",", ":")),
            json.dumps(b, sort_keys=True, separators=(",", ":")))

    def test_source_is_deterministic(self):
        self.assertEqual(len(demo_storageport.SOURCE), 65536)
        self.assertEqual(
            hashlib.sha256(demo_storageport.SOURCE).hexdigest(),
            demo_storageport.SOURCE_SHA256)


class TestStampContract(unittest.TestCase):
    def test_sha256_is_over_compact_body(self):
        p = demo_storageport.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        expect = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.assertEqual(p["sha256"], expect)
        self.assertEqual(p["self_integrity"]["contract"], "compact")


class TestPayloadShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = demo_storageport.run_demo()

    def test_header(self):
        self.assertEqual(self.p["demo"], "storageproof-demo@1.0.0")
        self.assertEqual(self.p["generated_at"], "2026-11-13T00:00:00Z")

    def test_six_drills_severity_sequence(self):
        self.assertEqual([d["name"] for d in self.p["drills"]], [
            "roundtrip-faithful", "range-read-resume",
            "absence-is-not-a-value", "stated-refusals",
            "degraded-mode-is-declared", "live-adapter-census"])
        self.assertEqual([d["severity"] for d in self.p["drills"]],
                         ["green", "green", "green", "green",
                          "amber", "green"])

    def test_adapter_census_pins(self):
        a = self.p["adapter"]
        self.assertEqual(a["adapter_main"], "opendal")
        self.assertEqual(a["adapter_active"], "opendal")
        self.assertFalse(a["degraded"])
        self.assertEqual(a["registry_entries"], 3)
        self.assertEqual(a["schemes"], ["fs", "memory", "s3"])


class TestDrillsAreReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = demo_storageport.run_demo()
        cls.d = {d["name"]: d for d in cls.p["drills"]}

    def test_roundtrip(self):
        d = self.d["roundtrip-faithful"]
        self.assertTrue(d["checksum_match"])
        self.assertEqual(d["bytes"], 65536)
        self.assertEqual(d["stat_length"], 65536)
        self.assertTrue(d["delete_idempotent_gone"])
        self.assertEqual(d["adapter"], "opendal")

    def test_range(self):
        d = self.d["range-read-resume"]
        self.assertTrue(d["range_match"])
        self.assertTrue(d["tail_match"])

    def test_absence(self):
        d = self.d["absence-is-not-a-value"]
        self.assertEqual(d["typed_absences"], 2)
        self.assertTrue(d["all_typed"])

    def test_refusals(self):
        d = self.d["stated-refusals"]
        self.assertEqual(d["refusals"], 2)
        self.assertTrue(d["capability_refusal_names_scheme"])
        self.assertTrue(d["credential_refusal_names_keys"])
        self.assertFalse(d["network_attempted"])

    def test_degraded(self):
        d = self.d["degraded-mode-is-declared"]
        self.assertTrue(d["fs_service_continues"])
        self.assertTrue(d["adapter_stamped"])
        self.assertTrue(d["cloud_refused_by_name"])

    def test_census(self):
        d = self.d["live-adapter-census"]
        self.assertEqual(d["adapter_active"], "opendal")
        self.assertIsNotNone(d["opendal_version"])
        self.assertEqual(d["retry_times"], 3)
        self.assertEqual(d["entries_requiring_secrets"],
                         ["cloud-archive"])


class TestFileEmission(unittest.TestCase):
    def test_file_bytes_equal_compact_dumps(self):
        demo_storageport.main()
        with open(demo_storageport.OUT, "rb") as fh:
            raw = fh.read()
        body = demo_storageport.run_demo()
        self.assertEqual(raw, json.dumps(
            body, sort_keys=True, separators=(",", ":")).encode())


if __name__ == "__main__":
    unittest.main()
