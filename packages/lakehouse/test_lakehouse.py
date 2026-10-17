import json
import os
import tempfile
import unittest

from lakehouse import build, HAVE_PARQUET, main


def fixture_events():
    return [
        {"id": "e1", "facility": "F-1", "type": "encounter", "ts": "2026-10-01T09:00:00Z",
         "payload": {"patient": "P-1", "diagnosis": "malaria"}},
        {"id": "e2", "facility": "F-1", "type": "claim_decided", "ts": "2026-10-01T10:00:00Z",
         "payload": {"claim_id": "C-1", "scheme": "BHCPF-VUL", "payable_ngn": 4500}},
        {"id": "e3", "facility": "F-2", "type": "claim_decided", "ts": "2026-10-02T11:00:00Z",
         "payload": {"claim_id": "C-2", "scheme": "BHCPF-VUL", "payable_ngn": 1500}},
        {"id": "e4", "facility": "F-2", "type": "encounter", "ts": "2026-10-02T12:00:00Z",
         "payload": {"patient": "P-9", "diagnosis": "anaemia"}},
    ]


class LakehouseTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def _read_partition(self, base):
        if os.path.exists(base + ".parquet"):
            import pyarrow.parquet as pq
            return pq.read_table(base + ".parquet").to_pylist()
        with open(base + ".jsonl") as fh:
            return [json.loads(l) for l in fh if l.strip()]

    def test_bronze_partitions_by_day(self):
        out = os.path.join(self.dir.name, "lake")
        result = build(fixture_events(), out)
        self.assertEqual(result["bronze_rows"], 4)
        d1 = self._read_partition(os.path.join(out, "bronze", "day=2026-10-01", "events"))
        d2 = self._read_partition(os.path.join(out, "bronze", "day=2026-10-02", "events"))
        self.assertEqual(len(d1), 2)
        self.assertEqual(len(d2), 2)

    def test_silver_canonical_tables(self):
        out = os.path.join(self.dir.name, "lake")
        build(fixture_events(), out)
        claims = self._read_partition(os.path.join(out, "silver", "type=claim_decided", "records"))
        self.assertEqual(len(claims), 2)
        self.assertEqual(claims[0]["facility"], "F-1")
        self.assertIn("scheme", claims[0])

    def test_gold_aggregates(self):
        out = os.path.join(self.dir.name, "lake")
        result = build(fixture_events(), out)
        agg = result["gold"]
        self.assertEqual(agg["claim_value_by_scheme_ngn"]["BHCPF-VUL"], 6000)
        self.assertEqual(agg["events_by_facility"], {"F-1": 2, "F-2": 2})
        with open(os.path.join(out, "gold", "aggregates.json")) as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk["events_by_type"]["encounter"], 2)

    def test_cli(self):
        out = os.path.join(self.dir.name, "lake")
        src = os.path.join(self.dir.name, "events.jsonl")
        with open(src, "w") as fh:
            for ev in fixture_events():
                fh.write(json.dumps(ev) + "\n")
        self.assertEqual(main(["build", "--events", src, "--out", out]), 0)

    def test_format_flag_reflects_pyarrow(self):
        out = os.path.join(self.dir.name, "lake")
        result = build(fixture_events(), out)
        self.assertEqual(result["format"], "parquet" if HAVE_PARQUET else "jsonl")


if __name__ == "__main__":
    unittest.main()
