"""Tests for shellproof — the shell gate (ADR-0082). Fixture tests run
against synthetic trees via the injected readers; TestRealTree is the
meta gate against the live repository."""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(_HERE, "..", "proofindex"))

import shellproof  # noqa: E402
import proofindex  # noqa: E402

REPO_ROOT = os.path.normpath(os.path.join(_HERE, "..", ".."))

FIXTURE_SW_A = 'const SHELL = ["./index.html", "./app.js", "../tokens.css"];\n'
FIXTURE_SW_B = 'const SHELL = ["./board.html", "./board.json"];\n'


def _fixture_tree(files):
    """files: iterable of repo-relative paths that 'exist'."""
    fs = set(files)
    return (lambda p: p in fs), (lambda: sorted(
        f for f in fs if f.endswith(".html")))


class TestFixtureAudit(unittest.TestCase):
    def _audit(self, files, payloads, exclusions=None):
        files = set(files) | {"apps/pwa/sw-a.js", "apps/pwa/sw-b.js"}
        exists, listdir = _fixture_tree(files)
        sources = {"apps/pwa/sw-a.js": FIXTURE_SW_A,
                   "apps/pwa/sw-b.js": FIXTURE_SW_B}
        return shellproof.audit(
            ".", shell_files=("apps/pwa/sw-a.js", "apps/pwa/sw-b.js"),
            payloads=payloads, exists=exists, listdir=listdir,
            read=lambda p: sources[p],
            exclusions=exclusions or {},
            )

    def test_all_green_when_everything_cached(self):
        files = {"apps/pwa/index.html", "apps/pwa/app.js",
                 "apps/tokens.css", "apps/pwa/board.html",
                 "apps/pwa/board.json"}
        rep = self._audit(files, ["apps/pwa/board.json"])
        self.assertTrue(rep["summary"]["all_installable"])
        self.assertTrue(rep["summary"]["full_coverage"])
        self.assertEqual(rep["install_killers"], [])
        self.assertEqual(rep["coverage"]["uncached"], [])

    def test_missing_entry_is_an_install_killer_named(self):
        files = {"apps/pwa/index.html", "apps/pwa/app.js",
                 "apps/pwa/board.html", "apps/pwa/board.json"}
        rep = self._audit(files, ["apps/pwa/board.json"])
        self.assertFalse(rep["summary"]["all_installable"])
        self.assertEqual(rep["install_killers"], ["apps/tokens.css"])
        entry = rep["shells"][0]["entries"][2]
        self.assertEqual(entry["verdict"], "missing")
        self.assertEqual(entry["url"], "../tokens.css")

    def test_uncached_payload_is_a_coverage_gap_named(self):
        files = {"apps/pwa/index.html", "apps/pwa/app.js",
                 "apps/tokens.css", "apps/pwa/board.html",
                 "apps/pwa/board.json", "apps/pwa/ghost.json"}
        rep = self._audit(files, ["apps/pwa/board.json",
                                  "apps/pwa/ghost.json"])
        self.assertTrue(rep["summary"]["all_installable"])
        self.assertFalse(rep["summary"]["full_coverage"])
        self.assertEqual(rep["coverage"]["uncached"],
                         ["apps/pwa/ghost.json"])

    def test_uncached_board_page_is_a_coverage_gap_named(self):
        files = {"apps/pwa/index.html", "apps/pwa/app.js",
                 "apps/tokens.css", "apps/pwa/board.html",
                 "apps/pwa/board.json", "apps/pwa/orphan.html"}
        rep = self._audit(files, ["apps/pwa/board.json"])
        self.assertEqual(rep["coverage"]["uncached"],
                         ["apps/pwa/orphan.html"])

    def test_exclusion_is_honored_and_named_with_reason(self):
        files = {"apps/pwa/index.html", "apps/pwa/app.js",
                 "apps/tokens.css", "apps/pwa/board.html",
                 "apps/pwa/board.json", "apps/pwa/probe.html"}
        rep = self._audit(files, ["apps/pwa/board.json"],
                          exclusions={"apps/pwa/probe.html":
                                      "diagnostic page"})
        self.assertTrue(rep["summary"]["full_coverage"])
        ex = rep["coverage"]["excluded"]
        self.assertEqual(ex[0]["path"], "apps/pwa/probe.html")
        self.assertEqual(ex[0]["reason"], "diagnostic page")


class TestRealTree(unittest.TestCase):
    """The meta gate: the live tree must pass its own audit."""

    def _live(self):
        payloads = [p["path"] for p in proofindex.PAYLOADS
                    if p["path"].startswith("apps/pwa/")]
        return shellproof.audit(REPO_ROOT, payloads=payloads)

    def test_every_shell_entry_resolves(self):
        rep = self._live()
        self.assertEqual(rep["install_killers"], [],
                         "install-killers: %s"
                         % rep["install_killers"])

    def test_every_payload_and_board_is_cached_or_excluded(self):
        rep = self._live()
        self.assertEqual(rep["coverage"]["uncached"], [],
                         "uncached: %s" % rep["coverage"]["uncached"])

    def test_declared_shells_exist_and_parse(self):
        rep = self._live()
        self.assertEqual(rep["summary"]["shells"], 2)
        self.assertGreater(rep["summary"]["shell_entries"], 0)

    def test_exclusions_are_the_declared_set(self):
        rep = self._live()
        self.assertEqual(
            [e["path"] for e in rep["coverage"]["excluded"]],
            ["apps/pwa/probe.html"])


if __name__ == "__main__":
    unittest.main()
