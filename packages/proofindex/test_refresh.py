"""Tests for the refresh rail (ADR-0081) — fixture executors only; the
real subprocess executor is exercised by the meta freshness gate reading
the same recipe book."""
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import refresh  # noqa: E402


def _write(root, rel, data):
    full = os.path.join(root, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as fh:
        fh.write(data)


class TestRunRecipes(unittest.TestCase):
    def _fixture(self, td):
        _write(td, "apps/pwa/alpha.json", b'{"v":1}')
        _write(td, "apps/pwa/beta.json", b'{"v":1}')
        _write(td, "apps/pwa/gamma.json", b'{"v":1}')
        return [
            {"name": "alpha", "package": "packages/x", "kind": "file",
             "write": "alpha", "paths": ["apps/pwa/alpha.json"]},
            {"name": "beta", "package": "packages/x", "kind": "file",
             "write": "beta", "paths": ["apps/pwa/beta.json"]},
            {"name": "gamma", "package": "packages/x", "kind": "file",
             "write": "gamma", "paths": ["apps/pwa/gamma.json"]},
            {"name": "delta", "package": "packages/x", "kind": "file",
             "write": "delta", "paths": ["apps/pwa/delta.json"]},
        ]

    def test_all_four_fates(self):
        with tempfile.TemporaryDirectory() as td:
            recipes = self._fixture(td)

            def executor(pkg, snippet):
                if snippet == "alpha":
                    _write(td, "apps/pwa/alpha.json", b'{"v":1}')
                elif snippet == "beta":
                    _write(td, "apps/pwa/beta.json", b'{"v":2}')
                elif snippet == "gamma":
                    raise RuntimeError("adapter down")
                # delta: runs fine but writes nothing

            rep = refresh.run_recipes(recipes, root=td,
                                      executor=executor)
        got = {x["name"]: x["status"] for x in rep["results"]}
        self.assertEqual(got, {"alpha": "already-fresh",
                               "beta": "regenerated",
                               "gamma": "runner-error",
                               "delta": "absent-after-write"})
        self.assertFalse(rep["summary"]["ok"])
        self.assertEqual(rep["summary"]["regenerated"], 1)
        self.assertEqual(rep["summary"]["already_fresh"], 1)

    def test_regenerated_names_the_changed_paths(self):
        with tempfile.TemporaryDirectory() as td:
            recipes = self._fixture(td)[:1] + [
                {"name": "multi", "package": "packages/x", "kind": "hash",
                 "write": "multi",
                 "paths": ["apps/pwa/m1.json", "apps/pwa/m2.json"]}]
            _write(td, "apps/pwa/m1.json", b"old")
            _write(td, "apps/pwa/m2.json", b"same")

            def executor(pkg, snippet):
                if snippet == "multi":
                    _write(td, "apps/pwa/m1.json", b"new")
                    _write(td, "apps/pwa/m2.json", b"same")

            rep = refresh.run_recipes(recipes, root=td,
                                      executor=executor)
        multi = rep["results"][1]
        self.assertEqual(multi["status"], "regenerated")
        self.assertEqual(multi["changed"], ["apps/pwa/m1.json"])
        self.assertEqual(set(multi["digests"]),
                         {"apps/pwa/m1.json", "apps/pwa/m2.json"})

    def test_ok_when_everything_fresh_or_regenerated(self):
        with tempfile.TemporaryDirectory() as td:
            recipes = self._fixture(td)[:1]

            def executor(pkg, snippet):
                _write(td, "apps/pwa/alpha.json", b'{"v":1}')

            rep = refresh.run_recipes(recipes, root=td,
                                      executor=executor)
        self.assertTrue(rep["summary"]["ok"])


class TestRecipeBook(unittest.TestCase):
    def test_recipes_cover_every_registered_payload(self):
        import proofindex
        import recipes
        covered = set()
        for r in recipes.RECIPES:
            covered.update(r["paths"])
        registered = {e["path"] for e in proofindex.PAYLOADS}
        self.assertEqual(registered - covered, set())
        self.assertEqual(covered - registered, set())

    def test_recipe_names_unique_and_kinds_closed(self):
        import recipes
        names = [r["name"] for r in recipes.RECIPES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual({r["kind"] for r in recipes.RECIPES},
                         {"file", "hash"})
        for r in recipes.RECIPES:
            if r["kind"] == "hash":
                self.assertIn("probe", r)


if __name__ == "__main__":
    unittest.main()
