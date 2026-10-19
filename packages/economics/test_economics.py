import unittest

import economics as ec


def enc(code, staff_min=30, kpm=50_00, consum=(), los=0, enrollee=None):
    return {
        "code": code, "enrollee_id": enrollee,
        "staff": [{"minutes": staff_min, "kobo_per_minute": kpm}],
        "consumables": [{"kobo": c} for c in consum],
        "los_days": los,
    }


class TestMarginReport(unittest.TestCase):
    def test_margin_per_code(self):
        # DEL-CS tariff 75,000_00; costs: 180min x 100kobo/min = 18,000_00
        # + 20,000_00 consumables + 2 days x 5,000_00 overhead = 48,000_00
        report = ec.margin_report(
            [enc("DEL-CS", 180, 100_00, [20000_00], 2),
             enc("DEL-CS", 180, 100_00, [20000_00], 2),
             enc("CONS-GEN", 20, 50_00)],
            overhead_kobo_per_day=5000_00)
        cs = next(l for l in report["lines"] if l["code"] == "DEL-CS")
        self.assertEqual(cs["revenue_kobo"], 2 * 75000_00)
        self.assertEqual(cs["cost_kobo"], 2 * (180 * 100_00 + 20000_00 + 2 * 5000_00))
        self.assertEqual(cs["margin_kobo"], cs["revenue_kobo"] - cs["cost_kobo"])
        self.assertGreater(cs["margin_pct"], 0)

    def test_unknown_code_flagged(self):
        report = ec.margin_report([enc("ZZZ-NOPE")])
        self.assertEqual(report["unknown_codes"], ["ZZZ-NOPE"])
        self.assertFalse(report["lines"][0]["in_tariff_schedule"])

    def test_missing_code_raises(self):
        with self.assertRaises(ec.EconomicsError):
            ec.margin_report([{"staff": []}])


class TestPanelPnL(unittest.TestCase):
    def test_risk_bands(self):
        self.assertEqual(ec.risk_band({"id": "a", "age": 2}), "under5")
        self.assertEqual(ec.risk_band({"id": "b", "age": 28, "pregnant": True}), "maternal")
        self.assertEqual(ec.risk_band({"id": "c", "age": 40, "chronic": True}), "chronic")
        self.assertEqual(ec.risk_band({"id": "d", "age": 70}), "elderly")

    def test_panel_margin(self):
        enrollees = [{"id": f"e{i}", "age": 30} for i in range(100)]
        encounters = [enc("CONS-GEN", 20, 50_00, enrollee="e1")] * 10
        pnl = ec.panel_pnl(enrollees, encounters, 1450_00)
        adult = next(b for b in pnl["bands"] if b["band"] == "adult")
        self.assertEqual(adult["capitation_kobo"], 100 * 1450_00)
        self.assertEqual(adult["care_cost_kobo"], 10 * 20 * 50_00)
        self.assertEqual(pnl["panel_size"], 100)
        self.assertGreater(pnl["total_margin_kobo"], 0)

    def test_walkins_exposed(self):
        pnl = ec.panel_pnl([{"id": "e1", "age": 30}],
                           [enc("CONS-GEN", 20, 50_00, enrollee="ghost")], 1450_00)
        self.assertEqual(pnl["walk_in_cost_kobo"], 20 * 50_00)


class TestDenialReport(unittest.TestCase):
    CLAIMS = [
        {"hmo": "HMO-A", "status": "paid", "amount_kobo": 1000_00},
        {"hmo": "HMO-A", "status": "denied", "denial_reason": "missing-referral", "amount_kobo": 4000_00},
        {"hmo": "HMO-A", "status": "denied", "denial_reason": "documentation-gap", "amount_kobo": 2000_00},
        {"hmo": "HMO-B", "status": "denied", "denial_reason": "not-covered", "amount_kobo": 3000_00},
        {"hmo": "HMO-B", "status": "paid", "amount_kobo": 1000_00},
    ]

    def test_classes_and_preventable_share(self):
        rep = ec.denial_report(self.CLAIMS)
        self.assertEqual(rep["denied"], 3)
        self.assertEqual(rep["by_reason"]["missing-referral"]["klass"], "preventable")
        self.assertEqual(rep["by_reason"]["not-covered"]["klass"], "package")
        # preventable = 4000+2000 of 9000 denied
        self.assertAlmostEqual(rep["preventable_share_pct"], 66.7, places=1)

    def test_hmo_denial_rates(self):
        rep = ec.denial_report(self.CLAIMS)
        self.assertEqual(rep["by_hmo"]["HMO-A"]["denial_rate_pct"], 66.7)
        self.assertEqual(rep["by_hmo"]["HMO-B"]["denial_rate_pct"], 50.0)


class TestTariffSimulation(unittest.TestCase):
    def test_cpi_compounding_and_uplift(self):
        sim = ec.simulate_tariff_change(
            [{"code": "DEL-CS", "volume": 10}],
            {2026: 0.15, 2027: 0.15}, 2027)
        line = sim["lines"][0]
        expected = round(75000_00 * 1.15 * 1.15)
        self.assertEqual(line["expected_tariff_kobo"], expected)
        self.assertEqual(sim["pool_outflow_delta_kobo"], (expected - 75000_00) * 10)
        self.assertEqual(sim["enrollee_copay_delta_kobo"], (expected - 75000_00) * 10 // 10)

    def test_rejects_past_year(self):
        with self.assertRaises(ec.EconomicsError):
            ec.simulate_tariff_change([], {}, 2024)

    def test_report_carries_vintage(self):
        rep = ec.margin_report([enc("CONS-GEN")])
        self.assertEqual(rep["tariff_year"], 2025)


if __name__ == "__main__":
    unittest.main()
