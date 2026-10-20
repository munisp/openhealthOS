import unittest

from dosagetext import SUPPORTED, render


def bd(freq=2, period=1, unit="d", value=2, med_unit="tablet",
       route=None, prn=False):
    d = {
        "timing": {"repeat": {"frequency": freq, "period": period,
                              "periodUnit": unit}},
        "doseAndRate": [{"doseQuantity": {"value": value, "unit": med_unit}}],
    }
    if route:
        d["route"] = {"text": route}
    if prn:
        d["asNeededBoolean"] = True
    return d


class TestRender(unittest.TestCase):
    def test_english_twice_daily(self):
        self.assertEqual(render(bd(), "en"), "Take 2 tablet twice daily.")

    def test_all_languages_render(self):
        for lang in SUPPORTED:
            out = render(bd(freq=1), lang)
            self.assertTrue(out.endswith("."))
            self.assertNotEqual(out, "")

    def test_pidgin_is_pidgin_not_english(self):
        self.assertEqual(render(bd(freq=3), "pcm"),
                         "Take 2 tablet three times every day.")

    def test_hausa_verb(self):
        self.assertTrue(render(bd(), "ha").startswith("Ka sha "))

    def test_route_appended(self):
        self.assertIn("by mouth", render(bd(route="Oral"), "en"))

    def test_prn_appended(self):
        self.assertIn("when needed", render(bd(prn=True), "en"))
        self.assertIn("when you need am", render(bd(prn=True), "pcm"))

    def test_unknown_frequency_falls_back_safely(self):
        out = render(bd(freq=7), "en")
        self.assertEqual(out, "Take this medicine exactly as the prescriber explained.")

    def test_missing_dose_falls_back(self):
        d = bd()
        d["doseAndRate"] = [{}]
        self.assertIn("prescriber", render(d, "en"))

    def test_unsupported_lang_defaults_english(self):
        self.assertEqual(render(bd(), "fr"), render(bd(), "en"))

    def test_weekly(self):
        self.assertIn("once weekly", render(bd(freq=1, unit="wk"), "en"))
        self.assertIn("kwa izu", render(bd(freq=1, unit="wk"), "ig"))


if __name__ == "__main__":
    unittest.main()
