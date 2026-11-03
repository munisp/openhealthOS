import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from drugkb import (FormularyError, check_iv_y_site, tpn_assess,  # noqa: E402
                    IV_RULES_VERSION, TPN_RULES_VERSION)


class TestIVYSite(unittest.TestCase):
    def test_known_incompatible_named(self):
        v = check_iv_y_site("ceftriaxone", "calcium-gluconate")
        self.assertEqual(v["verdict"], "incompatible")
        self.assertEqual(v["version"], IV_RULES_VERSION)

    def test_pair_order_irrelevant(self):
        self.assertEqual(
            check_iv_y_site("calcium-gluconate", "ceftriaxone")
            ["verdict"], "incompatible")

    def test_unrated_is_not_compatible(self):
        v = check_iv_y_site("obscuremycin", "rarezepam")
        self.assertEqual(v["verdict"], "unrated")
        self.assertIn("not compatibility", v["note"])

    def test_same_agent_is_compatible(self):
        self.assertEqual(check_iv_y_site("morphine", "morphine")
                         ["verdict"], "compatible")


class TestTPN(unittest.TestCase):
    BASE = dict(volume_ml=2000, dextrose_pct=10, amino_acid_pct=4,
                lipid_pct=2, calcium_mmol=5, phosphate_mmol=15,
                route="central")

    def test_central_bag_passes(self):
        out = tpn_assess(**self.BASE)
        self.assertEqual(out["route_verdict"], "route-ok")
        self.assertEqual(out["version"], TPN_RULES_VERSION)
        self.assertFalse(out["flags"])

    def test_hot_bag_refused_peripheral(self):
        out = tpn_assess(**dict(self.BASE, route="peripheral",
                                dextrose_pct=15, amino_acid_pct=5))
        self.assertEqual(out["route_verdict"], "refused-peripheral")
        self.assertTrue(any("peripheral" in f for f in out["flags"]))

    def test_calcium_phosphate_product_flags(self):
        out = tpn_assess(**dict(self.BASE, calcium_mmol=20,
                                phosphate_mmol=20))
        self.assertTrue(any("precipitation" in f for f in out["flags"]))

    def test_missing_component_refuses(self):
        with self.assertRaises(FormularyError):
            tpn_assess(**dict(self.BASE, calcium_mmol=None))
        with self.assertRaises(FormularyError):
            tpn_assess(**dict(self.BASE, volume_ml=0))
        with self.assertRaises(FormularyError):
            tpn_assess(**dict(self.BASE, route="subcutaneous"))


if __name__ == "__main__":
    unittest.main()
