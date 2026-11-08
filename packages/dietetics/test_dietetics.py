"""Tests for packages/dietetics — therapeutic diets and kitchen orders."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dietetics import (  # noqa: E402
    NutritionScreen, DietOrder, WardKitchenSheet, Error, DIETS,
    register_diet,
)


def screened(category_bmi=17.0):
    s = NutritionScreen("Pax-1", day=1, screener="nurse-ada")
    s.assess(bmi=category_bmi, weight_loss_pct=6.0, intake_reduced_days=3)
    return s


class TestScreening(unittest.TestCase):
    def test_screen_categories(self):
        s = screened(17.0)
        self.assertIn(s.result["category"],
                      ("moderate-acute-malnutrition", "at-risk"))

    def test_screen_gates_diet(self):
        s = NutritionScreen("Pax-1", day=1, screener="nurse-ada")
        s.assess(bmi=15.5, weight_loss_pct=12.0, intake_reduced_days=7)
        self.assertEqual(s.result["recommended_diet"], "f-75")

    def test_implausible_bmi_refused(self):
        s = NutritionScreen("Pax-2", day=1, screener="nurse-ada")
        with self.assertRaises(Error):
            s.assess(bmi=95.0)


class TestDietOrder(unittest.TestCase):
    def test_order_requires_screen(self):
        s = NutritionScreen("Pax-1", day=1, screener="nurse-ada")
        with self.assertRaises(Error):
            DietOrder("D1", "Pax-1", "ward-a", day=1,
                      prescriber="dr-eze", screen=s, diet_code="f-75")

    def test_unknown_diet_refused(self):
        with self.assertRaises(Error):
            DietOrder("D1", "Pax-1", "ward-a", day=1,
                      prescriber="dr-eze", screen=screened(),
                      diet_code="pizza")

    def test_allergy_contraindication_fail_closed(self):
        with self.assertRaises(Error):
            DietOrder("D1", "Pax-1", "ward-a", day=1,
                      prescriber="dr-eze", screen=screened(),
                      diet_code="rutf", allergies=["peanut-allergy"])

    def test_kitchen_ticket(self):
        o = DietOrder("D1", "Pax-1", "ward-a", day=1,
                      prescriber="dr-eze", screen=screened(),
                      diet_code="high-protein", portions_per_day=3)
        t = o.kitchen_ticket()
        self.assertEqual(t["portions"], 3)
        self.assertEqual(t["ward"], "ward-a")

    def test_stop_blocks_ticket(self):
        o = DietOrder("D1", "Pax-1", "ward-a", day=1,
                      prescriber="dr-eze", screen=screened(),
                      diet_code="diabetic")
        o.stop(by="dr-eze", day=2, reason="discharged")
        with self.assertRaises(Error):
            o.kitchen_ticket()


class TestKitchenSheet(unittest.TestCase):
    def test_manifest_aggregates(self):
        sheet = WardKitchenSheet("ward-a", day=1)
        sheet.add(DietOrder("D1", "Pax-1", "ward-a", day=1,
                            prescriber="dr-eze", screen=screened(),
                            diet_code="diabetic", portions_per_day=3))
        sheet.add(DietOrder("D2", "Pax-2", "ward-a", day=1,
                            prescriber="dr-eze", screen=screened(),
                            diet_code="diabetic", portions_per_day=3))
        sheet.add(DietOrder("D3", "Pax-3", "ward-a", day=1,
                            prescriber="dr-eze", screen=screened(),
                            diet_code="renal", portions_per_day=2))
        m = sheet.manifest()
        self.assertEqual(m["portions_by_diet"]["diabetic"], 6)
        self.assertEqual(m["portions_by_diet"]["renal"], 2)
        self.assertEqual(len(m["sha256"]), 64)

    def test_wrong_ward_refused(self):
        sheet = WardKitchenSheet("ward-a", day=1)
        with self.assertRaises(Error):
            sheet.add(DietOrder("D1", "Pax-1", "ward-b", day=1,
                                prescriber="dr-eze", screen=screened(),
                                diet_code="normal"))

    def test_register_custom_diet(self):
        register_diet("low-purine", "gout diet")
        self.assertIn("low-purine", DIETS)
        del DIETS["low-purine"]  # test hygiene


if __name__ == "__main__":
    unittest.main()
