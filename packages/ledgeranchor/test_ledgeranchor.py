"""test_ledgeranchor.py — the external-notary contract.

Doctrines under test:
- A whole-chain rewrite that recomputes the LOCAL anchors file passes
  auditlake's verify_anchor (auditlake's own stated residual) and is
  caught RED by the external receipt.
- Template backends refuse until two DISTINCT named approvers ratify;
  ratified fabric with no transport is a stated refusal, never a fake
  receipt.
- The string is the contract: roots and receipt roots are 64 lowercase
  hex or a named refusal; compare_digest never sees non-ASCII.
- The registry is data: ratified entries refuse overwrite, sourceless
  entries refuse registration.
- The demo is deterministic, self-stamped, and its flags all hold.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..", "auditlake")))

import auditlake
import ledgeranchor
from ledgeranchor import (AnchorError, NOTARY_BACKENDS, publish_root,
                          ratify_backend, register_backend,
                          verify_external)
import demo_ledgeranchor

R1 = "a" * 64


class RegistryTestCase(unittest.TestCase):
    """Snapshot/restore the process-global registry (v1p lesson) —
    dict AND each entry's mutable ratified_by/status."""

    def setUp(self):
        self._saved = {k: {**v, "ratified_by": list(v["ratified_by"])}
                       for k, v in NOTARY_BACKENDS.items()}

    def tearDown(self):
        NOTARY_BACKENDS.clear()
        NOTARY_BACKENDS.update(self._saved)


class TestBackends(RegistryTestCase):
    def test_registry_shape(self):
        self.assertEqual(NOTARY_BACKENDS["file-publication@1.0.0"]
                         ["status"], "ratified")
        self.assertEqual(NOTARY_BACKENDS["fabric-gateway@1.0.0"]
                         ["status"], "template")

    def test_register_refuses_overwrite(self):
        with self.assertRaises(AnchorError):
            register_backend("file-publication@1.0.0",
                             {"kind": "file", "status": "template",
                              "source": "x", "description": "y"})

    def test_register_refuses_sourceless(self):
        with self.assertRaises(AnchorError):
            register_backend("new-notary@1.0.0",
                             {"kind": "file", "status": "template",
                              "source": "", "description": "y"})

    def test_register_refuses_self_ratified(self):
        with self.assertRaises(AnchorError):
            register_backend("new-notary@1.0.0",
                             {"kind": "file", "status": "ratified",
                              "ratified_by": ["ada"],
                              "source": "x", "description": "y"})

    def test_register_accepts_template(self):
        register_backend("new-notary@1.0.0",
                         {"kind": "file", "status": "template",
                          "source": "ADR-0069", "description": "y"})
        self.assertEqual(NOTARY_BACKENDS["new-notary@1.0.0"]["status"],
                         "template")

    def test_ratify_needs_two_distinct(self):
        ratify_backend("fabric-gateway@1.0.0", by="ada")
        self.assertEqual(NOTARY_BACKENDS["fabric-gateway@1.0.0"]
                         ["status"], "template")
        with self.assertRaises(AnchorError):
            ratify_backend("fabric-gateway@1.0.0", by="ada")
        ratify_backend("fabric-gateway@1.0.0", by="bola")
        self.assertEqual(NOTARY_BACKENDS["fabric-gateway@1.0.0"]
                         ["status"], "ratified")


class TestPublish(RegistryTestCase):
    def test_template_refuses_with_named_gap(self):
        with self.assertRaises(AnchorError) as ctx:
            publish_root(R1, backend="fabric-gateway@1.0.0",
                         entries=5, ts="T")
        self.assertIn("TEMPLATE", str(ctx.exception))
        self.assertIn("2 more named approver", str(ctx.exception))

    def test_noncanonical_root_refused_before_anything(self):
        with self.assertRaises(AnchorError):
            publish_root("A" * 64, backend="file-publication@1.0.0",
                         entries=5, ts="T", sink="/dev/null")
        with self.assertRaises(AnchorError):
            publish_root("abc", backend="file-publication@1.0.0",
                         entries=5, ts="T", sink="/dev/null")

    def test_non_ascii_root_is_a_refusal_not_a_typeerror(self):
        with self.assertRaises(AnchorError):
            publish_root("¡" * 64, backend="file-publication@1.0.0",
                         entries=5, ts="T", sink="/dev/null")

    def test_file_publication_stamps_receipt(self):
        with tempfile.TemporaryDirectory() as td:
            sink = os.path.join(td, "offbox")
            rec = publish_root(R1, backend="file-publication@1.0.0",
                               entries=5, ts="T", sink=sink)
            self.assertRegex(rec["receipt_sha256"], r"^[0-9a-f]{64}$")
            body = {k: v for k, v in rec.items()
                    if k != "receipt_sha256"}
            digest = hashlib.sha256(json.dumps(
                body, sort_keys=True, separators=(",", ":"))
                .encode()).hexdigest()
            self.assertEqual(rec["receipt_sha256"], digest)
            line = open(sink).read().strip()
            self.assertEqual(json.loads(line)["root"], R1)

    def test_ratified_fabric_without_wire_is_stated(self):
        ratify_backend("fabric-gateway@1.0.0", by="ada")
        ratify_backend("fabric-gateway@1.0.0", by="bola")
        with self.assertRaises(AnchorError) as ctx:
            publish_root(R1, backend="fabric-gateway@1.0.0",
                         entries=5, ts="T")
        self.assertIn("no transport configured", str(ctx.exception))

    def test_ratified_fabric_with_wire_roundtrips(self):
        ratify_backend("fabric-gateway@1.0.0", by="ada")
        ratify_backend("fabric-gateway@1.0.0", by="bola")
        rec = publish_root(R1, backend="fabric-gateway@1.0.0",
                           entries=5, ts="T",
                           transport=lambda **kw: {"tx": "TX-1"})
        self.assertEqual(rec["tx"], "TX-1")

    def test_wire_without_tx_refused(self):
        ratify_backend("fabric-gateway@1.0.0", by="ada")
        ratify_backend("fabric-gateway@1.0.0", by="bola")
        with self.assertRaises(AnchorError):
            publish_root(R1, backend="fabric-gateway@1.0.0",
                         entries=5, ts="T",
                         transport=lambda **kw: {})


class TestVerifyExternal(RegistryTestCase):
    def test_unanchored_is_stated(self):
        v = verify_external(chain_root=R1, receipts=[])
        self.assertEqual(v["status"], "unanchored")
        self.assertEqual(v["severity"], "amber")

    def test_match_is_green(self):
        v = verify_external(chain_root=R1,
                            receipts=[{"notary": "n", "root": R1,
                                       "entries": 5, "ts": "T"}])
        self.assertEqual(v["status"], "externally-anchored")
        self.assertEqual(v["severity"], "green")

    def test_mismatch_is_history_rewritten(self):
        v = verify_external(chain_root="b" * 64,
                            receipts=[{"notary": "n", "root": R1,
                                       "entries": 5, "ts": "T"}])
        self.assertEqual(v["status"], "history-rewritten")
        self.assertEqual(v["severity"], "red")

    def test_non_ascii_receipt_root_is_a_verdict_not_a_typeerror(self):
        v = verify_external(chain_root=R1,
                            receipts=[{"notary": "n", "root": "¡" * 64,
                                       "entries": 5, "ts": "T"}])
        self.assertEqual(v["status"], "malformed")
        self.assertEqual(v["severity"], "red")

    def test_non_object_receipt_is_malformed(self):
        v = verify_external(chain_root=R1, receipts=["not a dict"])
        self.assertEqual(v["status"], "malformed")

    def test_uppercase_receipt_root_is_non_canonical(self):
        v = verify_external(chain_root=R1,
                            receipts=[{"notary": "n", "root": "A" * 64,
                                       "entries": 5, "ts": "T"}])
        self.assertEqual(v["status"], "malformed")
        self.assertIn("64 lowercase hex", v["detail"])


class TestRewriteCaughtExternally(RegistryTestCase):
    """The attack auditlake names in its own comments, demonstrated."""

    def test_whole_chain_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            chain = os.path.join(td, "audit.log")
            entries = []
            prev = auditlake.GENESIS
            for i in range(1, 4):
                e = {"seq": i, "ts": "T", "actor": "a", "action": "x",
                     "subject": "s", "details": {}, "source_id": f"a-{i}",
                     "prev_hash": prev}
                e["hash"] = auditlake.entry_hash(e)
                prev = e["hash"]
                entries.append(e)
                with open(chain, "a") as fh:
                    fh.write(json.dumps(e, sort_keys=True) + "\n")
            root1 = auditlake.merkle_root([e["hash"] for e in entries])
            rec = publish_root(root1, backend="file-publication@1.0.0",
                               entries=3, ts="T",
                               sink=os.path.join(td, "offbox"))

            # Attacker: new entries, recomputed chain, recomputed LOCAL
            # anchors file — everything on-box is consistent.
            rewritten = os.path.join(td, "rw.log")
            prev = auditlake.GENESIS
            hashes = []
            for i in range(1, 4):
                e = {"seq": i, "ts": "T", "actor": "evil",
                     "action": "x", "subject": "s", "details": {},
                     "source_id": f"evil-{i}", "prev_hash": prev}
                e["hash"] = auditlake.entry_hash(e)
                prev = e["hash"]
                hashes.append(e["hash"])
                with open(rewritten, "a") as fh:
                    fh.write(json.dumps(e, sort_keys=True) + "\n")
            root2 = auditlake.merkle_root(hashes)
            local = os.path.join(td, "anchors.local")
            with open(local, "w") as fh:
                fh.write(json.dumps({"ts": "T", "entries": 3,
                                     "root": root2}) + "\n")

            ok, _ = auditlake.verify_anchor(rewritten, local)
            self.assertTrue(ok)   # the honest gap, demonstrated
            v = verify_external(chain_root=root2, receipts=[rec])
            self.assertEqual(v["status"], "history-rewritten")
            v = verify_external(chain_root=root1, receipts=[rec])
            self.assertEqual(v["status"], "externally-anchored")


class TestDemoAnchorproof(unittest.TestCase):
    def test_deterministic_byte_identical(self):
        a = demo_ledgeranchor.run_demo()
        b = demo_ledgeranchor.run_demo()
        self.assertEqual(json.dumps(a, sort_keys=True),
                         json.dumps(b, sort_keys=True))

    def test_stamp_contract(self):
        p = demo_ledgeranchor.run_demo()
        body = {k: v for k, v in p.items() if k != "sha256"}
        digest = hashlib.sha256(json.dumps(
            body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(p["sha256"], digest)

    def test_all_flags_hold(self):
        p = demo_ledgeranchor.run_demo()
        for flag, holds in p["flags"].items():
            self.assertTrue(holds, flag)

    def test_file_emission(self):
        demo_ledgeranchor.main()
        out = os.path.normpath(demo_ledgeranchor.OUT)
        back = json.load(open(out))
        self.assertEqual(back["version"],
                         demo_ledgeranchor.DEMO_VERSION)

    def test_registry_restored_after_demo(self):
        demo_ledgeranchor.run_demo()
        self.assertEqual(NOTARY_BACKENDS["fabric-gateway@1.0.0"]
                         ["status"], "template")


if __name__ == "__main__":
    unittest.main()
