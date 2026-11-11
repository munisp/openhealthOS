"""Tests for refreshproof — the refresh-rail demo surface (ADR-0081)."""
import hashlib
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))

import demo_refreshproof  # noqa: E402


class TestRefreshProof(unittest.TestCase):
    def test_determinism(self):
        a = demo_refreshproof.run_demo()
        b = demo_refreshproof.run_demo()
        self.assertEqual(a, b)
        self.assertEqual(a["sha256"], b["sha256"])

    def test_stamp_contract(self):
        body = demo_refreshproof.run_demo()
        sha = body.pop("sha256")
        expect = hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(",", ":"))
            .encode()).hexdigest()
        self.assertEqual(sha, expect)

    def test_payload_shape(self):
        p = demo_refreshproof.run_demo()
        self.assertEqual(p["version"], "refreshproof-demo@1.0.0")
        self.assertEqual(p["census"]["payloads"], 32)
        self.assertEqual(p["census"]["recipes"], 25)
        self.assertEqual(p["census"]["file_kind"], 20)
        self.assertEqual(p["census"]["hash_kind"], 5)
        self.assertEqual(p["census"]["hash_kind_payloads"], 12)
        self.assertEqual([d["verdict"] for d in p["drills"]],
                         ["already-fresh", "regenerated",
                          "runner-error", "absent-after-write",
                          "no-recipe"])
        self.assertEqual(p["report"]["summary"],
                         {"total": 4, "regenerated": 1,
                          "already_fresh": 1, "runner_error": 1,
                          "absent_after_write": 1, "ok": False})

    def test_gate_is_real_regenerated_names_path_and_digests(self):
        p = demo_refreshproof.run_demo()
        regen = [x for x in p["report"]["results"]
                 if x["status"] == "regenerated"][0]
        self.assertEqual(regen["changed"], ["fixtures/beta.json"])
        self.assertRegex(regen["digests"]["fixtures/beta.json"],
                         r"^[0-9a-f]{12}$")

    def test_census_matches_real_recipe_book(self):
        """The demo's census is DATA about the real tree — pin it."""
        sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))
        import recipes
        kinds = {"file": 0, "hash": 0}
        paths = 0
        for r in recipes.RECIPES:
            kinds[r["kind"]] += 1
            paths += len(r["paths"])
        p = demo_refreshproof.run_demo()
        self.assertEqual(p["census"]["recipes"], len(recipes.RECIPES))
        self.assertEqual(p["census"]["file_kind"], kinds["file"])
        self.assertEqual(p["census"]["hash_kind"], kinds["hash"])
        self.assertEqual(p["census"]["payloads"], paths)

    def test_file_emission(self):
        demo_refreshproof.main()
        out = os.path.normpath(demo_refreshproof.OUT)
        with open(out, encoding="utf-8") as fh:
            back = json.load(fh)
        self.assertEqual(back["version"], demo_refreshproof.DEMO_VERSION)
        body = {k: v for k, v in back.items() if k != "sha256"}
        self.assertEqual(back["sha256"], demo_refreshproof._stamp(body))


if __name__ == "__main__":
    unittest.main()
