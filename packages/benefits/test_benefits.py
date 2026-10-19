import json
import subprocess
import sys
import unittest
from pathlib import Path

import benefits as b


class TestPackage(unittest.TestCase):
    def test_package_lists_services_with_tariffs(self):
        pkg = b.package_for("STATE-BASIC")
        self.assertEqual(pkg["capitation_kobo"], 1450_00)
        self.assertEqual(pkg["tariff_year"], 2025)
        codes = {s["code"] for s in pkg["services"]}
        self.assertIn("DEL-CS", codes)
        self.assertTrue(all("tariff_kobo" in s for s in pkg["services"]))

    def test_unknown_scheme_raises(self):
        with self.assertRaises(b.BenefitsError):
            b.package_for("NOPE")


class TestExplain(unittest.TestCase):
    def test_headline_and_categories(self):
        e = b.explain("BHCPF-VUL")
        self.assertIn("co-pay none", e["headline"])
        self.assertIn("maternal", e["categories"])
        self.assertGreater(e["categories"]["maternal"]["covered"], 0)
        self.assertIn("referral", e["referral_note"].lower())

    def test_state_basic_copay(self):
        e = b.explain("STATE-BASIC")
        self.assertIn("10%", e["headline"])


class TestCheck(unittest.TestCase):
    def test_covered_with_copay(self):
        r = b.check("STATE-BASIC", "DEL-CS")
        self.assertTrue(r["covered"])
        self.assertTrue(r["referral_required"])
        self.assertEqual(r["tariff_kobo"], 75000_00)
        self.assertEqual(r["patient_pays_kobo"], 7500_00)  # 10%

    def test_bhcpf_zero_copay(self):
        r = b.check("BHCPF-VUL", "ANC-001")
        self.assertEqual(r["patient_pays_kobo"], 0)

    def test_excluded_and_unknown(self):
        r = b.check("BHCPF-VUL", "REN-DIAL")
        self.assertFalse(r["covered"])
        r = b.check("STATE-BASIC", "COSMETIC-1")
        self.assertFalse(r["covered"])

    def test_adjudicator_alignment(self):
        """The enforcement fixture and the patient-facing catalogue must
        agree on which codes are covered — this is the core doctrine."""
        adj = {
            "BHCPF-VUL": {"MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB",
                          "AMX-SUS", "ITN-001", "LAB-HB"},
            "STATE-BASIC": {"MAL-RDT", "ANC-001", "ORS-001", "PARA-TAB",
                            "AMX-SUS", "LAB-HB", "LAB-MP"},
        }
        for scheme, codes in adj.items():
            for code in codes:
                self.assertTrue(b.check(scheme, code)["covered"],
                                f"{scheme}/{code} covered in adjudicator but not catalogue")


class TestStaleness(unittest.TestCase):
    def test_cpi_recompute(self):
        r = b.staleness(2027, {"2026": 0.15, "2027": 0.15})
        self.assertAlmostEqual(r["cpi_factor"], 1.3225, places=4)
        self.assertEqual(r["capitation_expected_kobo"], round(1450_00 * 1.3225))
        cxr = next(s for s in r["services"] if s["code"] == "RAD-CXR")
        self.assertEqual(cxr["expected_kobo"], round(1800_00 * 1.3225))
        self.assertEqual(cxr["gap_pct"], 32.2)  # round(32.25, 1) floats down

    def test_rejects_past_year(self):
        with self.assertRaises(b.BenefitsError):
            b.staleness(2024, {})


class TestCLI(unittest.TestCase):
    def run_cli(self, op):
        p = subprocess.run([sys.executable, str(Path(__file__).parent / "benefits.py")],
                           input=json.dumps(op), capture_output=True, text=True)
        return p.returncode, json.loads(p.stdout)

    def test_check_roundtrip(self):
        code, out = self.run_cli({"op": "check", "scheme": "STATE-BASIC", "code": "LAB-FBC"})
        self.assertEqual(code, 0)
        self.assertEqual(out["tariff_kobo"], 2600_00)

    def test_error_path(self):
        code, out = self.run_cli({"op": "check", "scheme": "BOGUS", "code": "X"})
        self.assertEqual(code, 1)
        self.assertIn("error", out)


if __name__ == "__main__":
    unittest.main()
