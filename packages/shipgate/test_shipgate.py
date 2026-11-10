"""test_shipgate.py — the readiness-gate contract.

Doctrines under test:
- The register is well-formed: unique ids, closed vocabularies, every
  remainder owned and closable-in-principle.
- Verdicts are three words: proven / stated-remainder /
  unsubstantiated. No "production ready".
- A claim without evidence is red. A remainder without an owner is
  refused at the door. The whole assessment fails closed on one bad
  entry — via a malformed REGISTER entry, not a skipped row.
- The demo surface is deterministic, self-stamped, and its drills are
  named.
"""
import json
import os
import unittest

import shipgate
from shipgate import (CAPABILITIES, EVIDENCE_KINDS, OWNERS,
                      SHIPGATE_VERSION, ShipgateError, assess,
                      validate_entry, verdict_of)
import demo_shipgate


def _entry(**over):
    base = {"id": "t", "title": "T", "layer": "l",
            "evidence": [{"kind": "test-suite", "ref": "tests"}],
            "outstanding": []}
    base.update(over)
    return base


class TestRegister(unittest.TestCase):
    """The standing register: well-formed, owned, substantiated."""

    def test_every_entry_validates(self):
        for e in CAPABILITIES:
            validate_entry(e)   # must not raise

    def test_unique_ids(self):
        ids = [e["id"] for e in CAPABILITIES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_unsubstantiated_capability(self):
        report = assess()
        self.assertEqual(report["summary"]["unsubstantiated"], 0)
        self.assertTrue(report["summary"]["all_substantiated"])

    def test_every_remainder_owned(self):
        report = assess()
        self.assertTrue(report["summary"]["all_remainders_owned"])
        for row in report["capabilities"]:
            for out in row["outstanding"]:
                self.assertIn(out["owner"], OWNERS)
                self.assertTrue(out["needs"])

    def test_evidence_kinds_closed(self):
        for e in CAPABILITIES:
            for ev in e["evidence"]:
                self.assertIn(ev["kind"], EVIDENCE_KINDS)

    def test_regulatory_row_exists_and_is_honest(self):
        reg = [e for e in CAPABILITIES if e["id"] == "regulatory"]
        self.assertEqual(len(reg), 1)
        # Regulatory clearance is NEVER green on this platform.
        self.assertEqual(verdict_of(reg[0]), "stated-remainder")
        self.assertTrue(reg[0]["outstanding"])


class TestVerdicts(unittest.TestCase):
    """Three words, no more."""

    def test_proven(self):
        self.assertEqual(verdict_of(_entry()), "proven")

    def test_stated_remainder(self):
        e = _entry(outstanding=[{"item": "live cert",
                                 "needs": "a real endpoint",
                                 "owner": "operations"}])
        self.assertEqual(verdict_of(e), "stated-remainder")

    def test_no_evidence_is_unsubstantiated(self):
        self.assertEqual(verdict_of(_entry(evidence=[])),
                         "unsubstantiated")

    def test_adjectives_do_not_help(self):
        e = _entry(title="World-class, best-in-breed, revolutionary",
                   evidence=[])
        self.assertEqual(verdict_of(e), "unsubstantiated")


class TestFailClosed(unittest.TestCase):
    """Malformed claims are refused, never skipped."""

    def test_missing_field_refused(self):
        e = _entry()
        del e["evidence"]
        with self.assertRaises(ShipgateError):
            validate_entry(e)

    def test_unknown_evidence_kind_refused(self):
        e = _entry(evidence=[{"kind": "vibes", "ref": "trust me"}])
        with self.assertRaises(ShipgateError) as ctx:
            validate_entry(e)
        self.assertIn("closed", str(ctx.exception))

    def test_evidence_without_ref_refused(self):
        e = _entry(evidence=[{"kind": "test-suite", "ref": ""}])
        with self.assertRaises(ShipgateError):
            validate_entry(e)

    def test_ownerless_remainder_refused(self):
        e = _entry(outstanding=[{"item": "cert", "needs": "device",
                                 "owner": "nobody"}])
        with self.assertRaises(ShipgateError) as ctx:
            validate_entry(e)
        self.assertIn("owner", str(ctx.exception))

    def test_unclosable_remainder_refused(self):
        e = _entry(outstanding=[{"item": "cert", "needs": "",
                                 "owner": "operations"}])
        with self.assertRaises(ShipgateError):
            validate_entry(e)

    def test_unnamed_remainder_refused(self):
        e = _entry(outstanding=[{"item": "", "needs": "x",
                                 "owner": "operations"}])
        with self.assertRaises(ShipgateError):
            validate_entry(e)

    def test_one_bad_entry_refuses_the_report(self):
        saved = shipgate.CAPABILITIES
        try:
            shipgate.CAPABILITIES = saved + [_entry(
                id="rogue",
                evidence=[{"kind": "marketing", "ref": "ad"}])]
            with self.assertRaises(ShipgateError):
                assess()
        finally:
            shipgate.CAPABILITIES = saved

    def test_duplicate_id_refused(self):
        saved = shipgate.CAPABILITIES
        try:
            shipgate.CAPABILITIES = saved + [dict(saved[0])]
            with self.assertRaises(ShipgateError) as ctx:
                assess()
            self.assertIn("duplicate", str(ctx.exception))
        finally:
            shipgate.CAPABILITIES = saved


class TestDemoShipproof(unittest.TestCase):
    """The demo surface: deterministic, self-stamped, drills named."""

    def test_deterministic_byte_identical(self):
        a = demo_shipgate.run_demo()
        b = demo_shipgate.run_demo()
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_stamp_contract(self):
        p = demo_shipgate.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        import hashlib
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True,
                       separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(p["sha256"], expect)
        self.assertRegex(p["sha256"], r"^[0-9a-f]{64}$")

    def test_payload_shape(self):
        p = demo_shipgate.run_demo()
        self.assertTrue(p["flags"]["all_substantiated"])
        self.assertTrue(p["flags"]["all_remainders_owned"])
        self.assertTrue(p["flags"]["drills_named"])
        self.assertEqual({d["verdict"] for d in p["drills"]},
                         {"unsubstantiated", "refused"})
        s = p["assessment"]["summary"]
        self.assertEqual(s["total"],
                         s["proven"] + s["stated_remainder"]
                         + s["unsubstantiated"])
        self.assertIn("why_demo", p["honesty"])

    def test_file_emission(self):
        demo_shipgate.main()
        out = os.path.normpath(demo_shipgate.OUT)
        with open(out) as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_shipgate.DEMO_VERSION)
        self.assertEqual(back["assessment"]["version"],
                         SHIPGATE_VERSION)


if __name__ == "__main__":
    unittest.main()
