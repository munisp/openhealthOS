import json
import os
import tempfile
import unittest

import schemainfer

EVENTS = [
    {"event_id": "e1", "ts": "2026-10-20T09:00:00Z", "facility_id": "PHC-1",
     "payload": {"kind": "claim", "amount": 5000}, "ok": True},
    {"event_id": "e2", "ts": "2026-10-20T09:01:00Z", "facility_id": "PHC-1",
     "payload": {"kind": "claim", "amount": None}, "ok": False},
    {"event_id": "e3", "ts": "2026-10-20T09:02:00Z", "facility_id": "PHC-2",
     "payload": {"kind": "dispense"}, "ok": True, "extra_sparse": "x"},
]


class TestInfer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "events.jsonl")
        with open(self.path, "w") as fh:
            for e in EVENTS:
                fh.write(json.dumps(e) + "\n")
        self.schema = schemainfer.infer(self.path, None, 0.5)

    def test_types_inferred(self):
        f = self.schema["fields"]
        self.assertEqual(f["ts"]["type"], "timestamp")
        self.assertEqual(f["payload.amount"]["type"], "number")
        self.assertEqual(f["ok"]["type"], "bool")
        self.assertEqual(f["facility_id"]["type"], "string")

    def test_presence_and_optional(self):
        f = self.schema["fields"]
        self.assertAlmostEqual(f["extra_sparse"]["presence"], 1 / 3, places=3)
        self.assertTrue(f["extra_sparse"]["optional"])
        self.assertFalse(f["event_id"]["optional"])

    def test_null_makes_nullable(self):
        f = self.schema["fields"]
        self.assertTrue(f["payload.amount"]["nullable"])

    def test_nested_object_is_child_table(self):
        f = self.schema["fields"]
        self.assertTrue(f["payload"]["child_table"])
        self.assertFalse(f["event_id"]["child_table"])

    def test_deterministic(self):
        again = schemainfer.infer(self.path, None, 0.5)
        self.assertEqual(self.schema["fields"], again["fields"])

    def test_sample_bounds_scan(self):
        s = schemainfer.infer(self.path, 2, 0.05)
        self.assertEqual(s["events_scanned"], 2)


class TestDiff(unittest.TestCase):
    def _schema(self, fields):
        return {"fields": {p: {"type": t, "presence": 1.0} for p, t in fields.items()}}

    def test_added_removed_retyped(self):
        old = self._schema({"a": "string", "b": "number", "c": "bool"})
        new = self._schema({"a": "string", "b": "string", "d": "timestamp"})
        out = schemainfer.diff(old, new)
        self.assertEqual([x["field"] for x in out["added"]], ["d"])
        self.assertEqual([x["field"] for x in out["removed"]], ["c"])
        self.assertEqual(out["retyped"][0], {"field": "b", "was": "number", "now": "string"})

    def test_no_drift_is_empty(self):
        s = self._schema({"a": "string"})
        out = schemainfer.diff(s, s)
        self.assertEqual(out, {"added": [], "removed": [], "retyped": []})


if __name__ == "__main__":
    unittest.main()
