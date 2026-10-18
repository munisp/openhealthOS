#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "a11y", pathlib.Path(__file__).parent / "a11y-contrast.py")
a11y = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a11y)


class TestContrast(unittest.TestCase):
    def test_luminance_and_ratio(self):
        self.assertAlmostEqual(a11y.luminance("#ffffff"), 1.0)
        self.assertAlmostEqual(a11y.contrast("#000000", "#ffffff"), 21.0, places=1)
        self.assertLess(a11y.contrast("#9ba8a7", "#ffffff"), 3.0)

    def test_scope_parsing(self):
        css = ':root{--ink:#000;--bg:#fff} [data-theme="ops"]{--text:#e6edf7;--bg:#050810}'
        s = a11y.scopes(css)
        self.assertIn(":root", s)
        self.assertIn('[data-theme="ops"]', s)
        self.assertEqual(s[":root"]["--ink"], "#000")

    def test_gate_logic(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".css", delete=False) as f:
            f.write(':root{--text:#777;--bg:#fff;--muted:#777}')
            path = f.name
        try:
            self.assertTrue(a11y.check_file(path))  # 4.48:1 — fails AA
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
