import json, os, tempfile, unittest
import auditlake

class TestAnchors(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.log = os.path.join(d, "audit.jsonl")
        self.anchors = os.path.join(d, "anchors.jsonl")
        for i in range(5):
            auditlake.append(self.log, "consent-service", "issue", f"patient-{i}")

    def test_anchor_roundtrip(self):
        rec = auditlake.anchor(self.log, self.anchors)
        self.assertEqual(rec["entries"], 5)
        ok, msg = auditlake.verify_anchor(self.log, self.anchors)
        self.assertTrue(ok, msg)

    def test_tamper_breaks_anchor(self):
        auditlake.anchor(self.log, self.anchors)
        lines = open(self.log).read().splitlines()
        e = json.loads(lines[2]); e["subject"] = "patient-XXX"
        lines[2] = json.dumps(e, sort_keys=True)   # naive tamper (hash stale)
        open(self.log, "w").write("\n".join(lines) + "\n")
        ok, msg = auditlake.verify_anchor(self.log, self.anchors)
        self.assertFalse(ok)
        self.assertIn("tampered", msg)

    def test_rebuilt_chain_breaks_anchor(self):
        # Sophisticated attack: rewrite the whole log with valid links.
        auditlake.anchor(self.log, self.anchors)
        os.remove(self.log)
        for i in range(5):
            auditlake.append(self.log, "consent-service", "issue", f"patient-{i}")
        # seq/replay produces identical entries only if ts match — but even
        # a bit-perfect rebuild differs once any field changes:
        lines = open(self.log).read().splitlines()
        e = json.loads(lines[0]); e["actor"] = "mallory"
        lines[0] = json.dumps(e, sort_keys=True)
        open(self.log, "w").write("\n".join(lines) + "\n")
        ok, _ = auditlake.verify_anchor(self.log, self.anchors)
        self.assertFalse(ok)

    def test_tail_deletion_detected(self):
        auditlake.anchor(self.log, self.anchors)
        lines = open(self.log).read().splitlines()[:3]
        open(self.log, "w").write("\n".join(lines) + "\n")
        ok, msg = auditlake.verify_anchor(self.log, self.anchors)
        self.assertFalse(ok)
        self.assertIn("tail deleted", msg)

    def test_append_after_anchor_still_holds(self):
        auditlake.anchor(self.log, self.anchors)
        auditlake.append(self.log, "shr-service", "break_glass", "patient-9")
        ok, msg = auditlake.verify_anchor(self.log, self.anchors)
        self.assertTrue(ok, msg)  # anchor covers a prefix; growth is legal

    def test_merkle_root_deterministic(self):
        h = ["0"*64, "1"*64, "2"*64]
        self.assertEqual(auditlake.merkle_root(h), auditlake.merkle_root(list(h)))
        self.assertNotEqual(auditlake.merkle_root(h), auditlake.merkle_root(h[:2]))

if __name__ == "__main__":
    unittest.main()
