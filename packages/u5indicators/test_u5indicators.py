import unittest
from u5indicators import mortality_rates, to_measure_report, IndicatorError


class TestRates(unittest.TestCase):
    def setUp(self):
        self.rates = mortality_rates(
            live_births=500,
            deaths_by_band={"neonatal": 8, "infant": 20,
                            "child-1-59m": 15, "stillbirth": 6},
            period={"start": "2026-01-01", "end": "2026-12-31"},
            facility="phc-1")

    def test_rates_per_1000(self):
        self.assertEqual(self.rates["nmr_per_1000"], 16.0)
        self.assertEqual(self.rates["imr_per_1000"], 40.0)
        self.assertEqual(self.rates["u5mr_per_1000"], 70.0)

    def test_stillbirth_counted_separately(self):
        self.assertEqual(self.rates["deaths"]["stillbirth"], 6)
        # stillbirths never enter NMR: 8 neonatal deaths, not 14
        self.assertEqual(self.rates["deaths"]["neonatal"], 8)

    def test_zero_denominator_refused(self):
        with self.assertRaises(IndicatorError) as cm:
            mortality_rates(live_births=0, deaths_by_band={},
                            period={}, facility="phc-1")
        self.assertIn("fiction", str(cm.exception))

    def test_infant_must_include_neonatal(self):
        with self.assertRaises(IndicatorError):
            mortality_rates(live_births=100,
                            deaths_by_band={"neonatal": 10, "infant": 5},
                            period={}, facility="phc-1")

    def test_measure_report_carries_populations(self):
        rep = to_measure_report(self.rates)
        self.assertEqual(rep["resourceType"], "MeasureReport")
        nmr = rep["group"][0]
        pops = {p["code"]["text"]: p["count"] for p in nmr["population"]}
        self.assertEqual(pops["numerator"], 8)
        self.assertEqual(pops["denominator"], 500)
        self.assertEqual(nmr["measureScore"]["value"], 16.0)

    def test_invisibility_caveat_attached(self):
        rep = to_measure_report(self.rates, invisibility={
            "by_kind": {"birth": {"unregistered_unqueued": 40},
                        "death": {"unregistered_unqueued": 3}}})
        self.assertEqual(rep["contained_note"]["unregistered_unqueued_births"], 40)
        self.assertIn("understates", rep["contained_note"]["caveat"])


if __name__ == "__main__":
    unittest.main()
