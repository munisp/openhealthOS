"""Tests for packages/localepack — country-context registry."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from localepack import (  # noqa: E402
    LOCALES, Error, context_summary, get_locale, locales,
    seed_population, seed_scenario,
)


class TestRegistry(unittest.TestCase):
    def test_ng_and_cv_present(self):
        self.assertIn("ng", LOCALES)
        self.assertIn("cv", LOCALES)

    def test_unknown_locale_refused(self):
        with self.assertRaises(Error):
            get_locale("xx")

    def test_ng_context(self):
        c = context_summary("ng")
        self.assertEqual(c["currency"], "NGN")
        self.assertEqual(c["payer"], "NHIA")
        self.assertEqual(c["vaccine_schedule"], "NPI")
        self.assertIn("en", c["languages"])

    def test_cv_context(self):
        c = context_summary("cv")
        self.assertEqual(c["country"], "Cabo Verde")
        self.assertEqual(c["currency"], "CVE")
        self.assertEqual(c["payer"], "INPS-CV")
        self.assertEqual(c["vaccine_schedule"], "PNV-CV")
        self.assertIn("pt", c["languages"])

    def test_cv_currency_peg(self):
        cv = get_locale("cv")
        self.assertEqual(cv["currency"]["peg"]["to"], "EUR")
        self.assertEqual(cv["currency"]["peg"]["rate"], 110.265)


class TestDeterministicSeeding(unittest.TestCase):
    def test_same_seed_same_population(self):
        p1 = seed_population("ng", n=10, seed=7)
        p2 = seed_population("ng", n=10, seed=7)
        self.assertEqual(p1, p2)

    def test_different_locale_different_population(self):
        ng = seed_population("ng", n=5, seed=7)
        cv = seed_population("cv", n=5, seed=7)
        ng_names = {p["name"] for p in ng}
        cv_names = {p["name"] for p in cv}
        self.assertTrue(ng_names.isdisjoint(cv_names))
        # NG phones start +234, CV start +238
        self.assertTrue(all(p["phone"].startswith("+234") for p in ng))
        self.assertTrue(all(p["phone"].startswith("+238") for p in cv))

    def test_deterministic_scenario_checksum(self):
        s1 = seed_scenario("cv", "campus-pilot", seed=42)
        s2 = seed_scenario("cv", "campus-pilot", seed=42)
        self.assertEqual(s1["sha256"], s2["sha256"])

    def test_cv_campus_scenario(self):
        s = seed_scenario("cv", "campus-pilot", seed=42)
        self.assertEqual(s["country"], "Cabo Verde")
        self.assertEqual(s["spec"]["anchor"]["id"], "CV-HAN")
        self.assertEqual(len(s["spec"]["members"]), 30)
        self.assertEqual(s["spec"]["premium_currency"], "CVE")
        self.assertEqual(s["spec"]["audience_label"],
                         "Universidade de Cabo Verde")

    def test_ng_campus_scenario(self):
        s = seed_scenario("ng", "campus-pilot", seed=42)
        self.assertEqual(s["spec"]["anchor"]["id"], "NG-LUTH")
        self.assertEqual(len(s["spec"]["members"]), 40)
        self.assertEqual(s["spec"]["premium_currency"], "NGN")

    def test_unknown_scenario_refused(self):
        with self.assertRaises(Error):
            seed_scenario("ng", "mars-mission")

    def test_ministry_demo(self):
        s = seed_scenario("cv", "ministry-demo")
        self.assertIn("VHC", s["spec"]["focus"])


if __name__ == "__main__":
    unittest.main()
