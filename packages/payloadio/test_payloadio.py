"""Tests for payloadio — the atomic-write seam (ADR-0088).

Includes TestAdoption, the gate that keeps the estate on the seam:
every writer of a registered stamped payload routes through
payloadio.write_text, and raw write-opens in writer modules are
declared exceptions or they are red.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "proofindex"))
import payloadio  # noqa: E402
import proofindex  # noqa: E402

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__),
                                          "..", ".."))

#: Adoption census data lives in the seam module (single home): the
#: gate, the demo, and this comment all read it from payloadio.
DELEGATES = payloadio.DELEGATES
DECLARED_RAW_EXCEPTIONS = payloadio.DECLARED_RAW_EXCEPTIONS

_RAW_WRITE_RE = re.compile(r'open\([^\n]*"w"')


def _writer_modules():
    """The census: every module that lands a registered (or declared
    self-excluded) stamped payload, delegates resolved."""
    exporters = {e["exporter"] for e in proofindex.PAYLOADS}
    exporters.add("packages/proofindex/demo_proofindex.py")
    modules = set()
    for exp in exporters:
        modules.add(DELEGATES.get(exp, exp))
    return sorted(modules)


class TestWriteText(unittest.TestCase):

    def test_lands_exact_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "p.json")
            text = json.dumps({"b": "é", "a": [1, 2]}, sort_keys=True,
                              ensure_ascii=False)
            payloadio.write_text(target, text)
            with open(target, "rb") as fh:
                self.assertEqual(fh.read(), text.encode("utf-8"))

    def test_no_tmp_residue_and_suffix_is_not_json(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "p.json")
            payloadio.write_text(target, "{}")
            self.assertEqual(os.listdir(td), ["p.json"])
        self.assertFalse(payloadio.TMP_SUFFIX.endswith(".json"))

    def test_missing_parent_is_loud(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(OSError):
                payloadio.write_text(os.path.join(td, "nope", "p.json"),
                                     "{}")

    def test_crash_mid_write_leaves_last_good(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "p.json")
            payloadio.write_text(target, '{"v": 1, "sha256": "ab"}')
            # crash: tmp written, replace never happens
            with open(os.path.join(td, ".payloadio-dead"
                                   + payloadio.TMP_SUFFIX), "w") as fh:
                fh.write('{"v": 2, partial')
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["v"], 1)

    def test_overwrite_replaces_atomically(self):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "p.json")
            payloadio.write_text(target, '{"v": 1}')
            payloadio.write_text(target, '{"v": 2}')
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["v"], 2)

    def test_concurrent_rewrite_never_tears(self):
        """Threaded rewriter + concurrent reader: every read is either
        the old payload or the new one — never torn, never absent.
        Deterministic by POSIX rename semantics, not by timing luck."""
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "p.json")
            payloadio.write_text(target, json.dumps({"gen": 0}))
            stop = []
            failures = []

            def rewriter():
                n = 0
                while not stop:
                    n += 1
                    payloadio.write_text(target, json.dumps({"v": n}))

            t = threading.Thread(target=rewriter)
            t.start()
            for _ in range(300):
                with open(target, encoding="utf-8") as fh:
                    try:
                        json.load(fh)
                    except (OSError, ValueError) as exc:
                        failures.append(str(exc))
            stop.append(1)
            t.join()
            self.assertEqual(failures, [])


class TestAdoption(unittest.TestCase):
    """The adoption gate: the census of payload writers is registry
    data, and every one of them routes through the seam. A new exporter
    that lands a payload with a raw write is RED here — a seam
    unadopted by its own estate is decoration."""

    def test_census_pins(self):
        modules = _writer_modules()
        self.assertEqual(len(modules), 29)
        self.assertEqual(len(proofindex.PAYLOADS), 35)
        self.assertIn("packages/payloadio/demo_payloadio.py", modules)

    def test_every_writer_routes_through_the_seam(self):
        missing = []
        for module in _writer_modules():
            with open(os.path.join(REPO_ROOT, module),
                      encoding="utf-8") as fh:
                src = fh.read()
            if "import payloadio" not in src \
                    or "payloadio.write_text(" not in src:
                missing.append(module)
        self.assertEqual(missing, [])

    def test_raw_writes_are_declared_exceptions_or_red(self):
        offenders = []
        total_raw = 0
        for module in _writer_modules():
            with open(os.path.join(REPO_ROOT, module),
                      encoding="utf-8") as fh:
                src = fh.read()
            hits = len(_RAW_WRITE_RE.findall(src))
            total_raw += hits
            if hits and module not in {
                    e["module"] for e in DECLARED_RAW_EXCEPTIONS}:
                offenders.append(module)
        self.assertEqual(offenders, [])
        self.assertEqual(total_raw,
                         sum(e["sites"] for e in DECLARED_RAW_EXCEPTIONS))
        self.assertTrue(all(e["reason"] for e in DECLARED_RAW_EXCEPTIONS))

    def test_delegates_exist_and_carry_the_write(self):
        for exporter, delegate in DELEGATES.items():
            self.assertTrue(os.path.exists(
                os.path.join(REPO_ROOT, delegate)))
            with open(os.path.join(REPO_ROOT, delegate),
                      encoding="utf-8") as fh:
                self.assertIn("payloadio.write_text(", fh.read())


if __name__ == "__main__":
    unittest.main()
