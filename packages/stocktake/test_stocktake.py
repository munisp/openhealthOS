#!/usr/bin/env python3
import json
import os
import tempfile
import unittest

from stocktake import (
    MATCH, OVER, SHORT, UNCOUNTED, UNKNOWN,
    ScanEvent, apply_barcode_map, drop_double_scans, reconcile,
    reconciliation_draft, summarize, main,
)


def scan(item="", barcode="", qty=1, actor="n1", at="2026-10-20T09:00:00Z", session="S1"):
    return ScanEvent(session=session, item_code=item, barcode=barcode,
                     qty=qty, actor=actor, location="ph-a", at=at)


class TestParse(unittest.TestCase):
    def test_defaults_qty_one(self):
        ev = ScanEvent.parse({"barcode": "B1", "item_code": "PCM-500"})
        self.assertEqual(ev.qty, 1.0)

    def test_identity_required(self):
        self.assertIsNone(ScanEvent.parse({"qty": 5}))
        self.assertIsNone(ScanEvent.parse({"item_code": "X", "qty": 0}))
        self.assertIsNone(ScanEvent.parse({"item_code": "X", "qty": "lots"}))

    def test_barcode_map_resolves(self):
        evs = [scan(barcode="890123")]
        apply_barcode_map(evs, {"890123": "PCM-500"})
        self.assertEqual(evs[0].item_code, "PCM-500")
        # explicit item_code wins over the map
        evs = [scan(item="REAL", barcode="890123")]
        apply_barcode_map(evs, {"890123": "PCM-500"})
        self.assertEqual(evs[0].item_code, "REAL")


class TestDoubleScan(unittest.TestCase):
    def test_rapid_duplicate_dropped(self):
        evs = [
            scan(item="A", barcode="B", at="2026-10-20T09:00:00Z"),
            scan(item="A", barcode="B", at="2026-10-20T09:00:02Z"),
        ]
        kept, warned = drop_double_scans(evs, 3)
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(warned), 1)

    def test_distinct_boxes_kept(self):
        evs = [
            scan(item="A", barcode="B1", at="2026-10-20T09:00:00Z"),
            scan(item="A", barcode="B2", at="2026-10-20T09:00:01Z"),
        ]
        kept, warned = drop_double_scans(evs, 3)
        self.assertEqual(len(kept), 2)
        self.assertEqual(warned, [])

    def test_late_rescan_kept(self):
        evs = [
            scan(item="A", barcode="B", at="2026-10-20T09:00:00Z"),
            scan(item="A", barcode="B", at="2026-10-20T09:05:00Z"),
        ]
        kept, _ = drop_double_scans(evs, 3)
        self.assertEqual(len(kept), 2)


class TestReconcile(unittest.TestCase):
    def test_verdicts(self):
        book = {"A": 100, "B": 50, "C": 10, "D": 7}
        counts = {"A": 100, "B": 45, "C": 14, "E": 3}
        vs = {v.item_code: v for v in reconcile(book, counts, 0, 0)}
        self.assertEqual(vs["A"].verdict, MATCH)
        self.assertEqual(vs["B"].verdict, SHORT)
        self.assertEqual(vs["B"].difference, -5)
        self.assertEqual(vs["C"].verdict, OVER)
        self.assertEqual(vs["D"].verdict, UNCOUNTED)
        self.assertEqual(vs["E"].verdict, UNKNOWN)

    def test_tolerance(self):
        vs = {v.item_code: v for v in reconcile({"A": 100}, {"A": 98}, 0, 2)}
        self.assertEqual(vs["A"].verdict, MATCH)
        vs = {v.item_code: v for v in reconcile({"A": 100}, {"A": 97}, 0, 2)}
        self.assertEqual(vs["A"].verdict, SHORT)
        vs = {v.item_code: v for v in reconcile({"A": 100}, {"A": 99.5}, 1, 0)}
        self.assertEqual(vs["A"].verdict, MATCH)


class TestDraftAndSummary(unittest.TestCase):
    def test_draft_only_corrections(self):
        vs = reconcile({"A": 100, "B": 50}, {"A": 100, "B": 40}, 0, 0)
        draft = reconciliation_draft(vs, "pharmacy-a", "2026-10-20")
        self.assertEqual(draft["doctype"], "Stock Reconciliation")
        self.assertEqual(len(draft["items"]), 1)
        self.assertEqual(draft["items"][0]["item_code"], "B")
        self.assertEqual(draft["items"][0]["qty"], 40)
        self.assertIn("stocktake:2026-10-20:pharmacy-a", draft["openhealth_id"])

    def test_summary(self):
        vs = reconcile({"A": 100, "B": 50}, {"A": 90, "B": 55}, 0, 0)
        s = summarize(vs)
        self.assertEqual(s["short_units"], 10)
        self.assertEqual(s["over_units"], 5)
        self.assertEqual(s["verdicts"], {"OVER": 1, "SHORT": 1})


class TestCLI(unittest.TestCase):
    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            scans = os.path.join(d, "scans.jsonl")
            book = os.path.join(d, "book.jsonl")
            recon = os.path.join(d, "recon.json")
            with open(scans, "w") as fh:
                fh.write('{"session":"S1","item_code":"A","qty":48,"actor":"n1","at":"2026-10-20T09:00:00Z"}\n')
                fh.write('{"session":"S1","barcode":"999","qty":3,"actor":"n1","at":"2026-10-20T09:01:00Z"}\n')
                fh.write('not json\n')
            with open(book, "w") as fh:
                fh.write('{"item_code":"A","qty":50}\n')
            rc = main(["count", "--scans", scans, "--book", book,
                       "--session", "S1", "--warehouse", "ph-a",
                       "--recon-out", recon])
            self.assertEqual(rc, 0)
            draft = json.load(open(recon))
            self.assertEqual(draft["items"][0]["qty"], 48)


if __name__ == "__main__":
    unittest.main()
