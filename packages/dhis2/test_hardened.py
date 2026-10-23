#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from dhis2map import Mapping  # noqa: E402
from hardened import (HardenedError, ResendGuard, coverage_report,  # noqa: E402
                      parse_import_summary, replay_spool, spool_append)

P1 = {"dataValues": [{"dataElement": "DE1", "period": "202609",
                      "orgUnit": "OU1", "value": 12}]}
P2 = {"dataValues": [{"dataElement": "DE2", "period": "202609",
                      "orgUnit": "OU1", "value": 7}]}


class TestSpool(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.spool = os.path.join(self.dir, "dhis2", "spool.jsonl")

    def test_roundtrip_in_order(self):
        spool_append(self.spool, P1)
        spool_append(self.spool, P2)
        seen = []
        out = replay_spool(self.spool, lambda p: seen.append(p) or {"ok": 1})
        self.assertEqual(out["pushed"], 2)
        self.assertEqual(seen, [P1, P2])

    def test_tampered_line_aborts_replay(self):
        spool_append(self.spool, P1)
        spool_append(self.spool, P2)
        lines = open(self.spool).read().splitlines()
        rec = json.loads(lines[1])
        rec["payload"]["dataValues"][0]["value"] = 999  # quiet edit
        lines[1] = json.dumps(rec, sort_keys=True)
        open(self.spool, "w").write("\n".join(lines) + "\n")
        with self.assertRaises(HardenedError) as ctx:
            replay_spool(self.spool, lambda p: None)
        self.assertIn("integrity", str(ctx.exception))

    def test_malformed_line_aborts_replay(self):
        spool_append(self.spool, P1)
        open(self.spool, "a").write("{not json\n")
        with self.assertRaises(HardenedError) as ctx:
            replay_spool(self.spool, lambda p: None)
        self.assertIn("malformed", str(ctx.exception))


class TestImportSummary(unittest.TestCase):
    def test_clean_import(self):
        out = parse_import_summary(
            {"importCount": {"imported": 10, "updated": 2, "ignored": 0,
                             "deleted": 0}})
        self.assertTrue(out["ok"])
        self.assertEqual(out["accepted"], 10)

    def test_conflicts_surface_and_fail(self):
        out = parse_import_summary(
            {"importCount": {"imported": 3, "updated": 0, "ignored": 1,
                             "deleted": 0},
             "conflicts": [{"object": "OU1", "value": "Period is locked"}]})
        self.assertFalse(out["ok"])
        self.assertEqual(out["conflicts"][0]["value"], "Period is locked")

    def test_ignored_without_conflict_is_warning(self):
        out = parse_import_summary(
            {"importCount": {"imported": 5, "updated": 0, "ignored": 2,
                             "deleted": 0}})
        self.assertTrue(out["ok"])
        self.assertEqual(len(out["warnings"]), 1)


class TestResendGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.guard = ResendGuard(os.path.join(self.dir, "manifest.json"))

    def test_new_then_duplicate(self):
        self.assertEqual(self.guard.check(P1), "new")
        self.guard.record(P1)
        self.assertEqual(self.guard.check(P1), "duplicate")

    def test_changed_payload_is_supersede(self):
        self.guard.record(P1)
        changed = json.loads(json.dumps(P1))
        changed["dataValues"][0]["value"] = 99
        self.assertEqual(self.guard.check(changed), "supersede")

    def test_manifest_survives_restart(self):
        self.guard.record(P1)
        guard2 = ResendGuard(self.guard.path)
        self.assertEqual(guard2.check(P1), "duplicate")

    def test_empty_payload_refused(self):
        with self.assertRaises(HardenedError):
            self.guard.check({"dataValues": []})


class TestCoverage(unittest.TestCase):
    def test_coverage_report(self):
        m = Mapping(org_units={"FAC-A": "OU1"},
                    data_elements={"malaria_cases": "DE1",
                                   "malaria_rate": "DE2"})
        out = coverage_report(m, ["FAC-A", "FAC-B"],
                              ["malaria", "anc"])
        self.assertFalse(out["ready"])
        self.assertEqual(out["facilities_unmapped"], ["FAC-B"])
        self.assertEqual(out["measures_unmapped"], ["anc"])
        self.assertEqual(out["facilities_mapped"], 1)


if __name__ == "__main__":
    unittest.main()
