"""Tests for vhsales — group manifests that validate before anyone is
charged, and a partner door with hashed keys and attributed sales."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhcover import KeyRing, VHCCoverRegistry  # noqa: E402
from vhsales import (PartnerRegistry, SalesError,  # noqa: E402
                     purchase_group)

CV = "cv-vhc@1.0.0"


def _cv():
    ring = KeyRing()
    ring.register("k1", "secret-one", active_from=0)
    return VHCCoverRegistry(profile_version=CV, key_ring=ring,
                            issuer="cv-vhc")


class TestGroupPurchase(unittest.TestCase):
    def test_manifest_prices_per_pax(self):
        vhc = _cv()
        out = purchase_group(
            vhc, payer_ref="TOURCO-1", entry_day=10, day=1, key_id="k1",
            travelers=[
                {"passport_ref": "P1", "full_name": "Ad Ult",
                 "age_years": 35, "days": 40},     # 2 blocks, €50
                {"passport_ref": "P2", "full_name": "Kid Ten",
                 "age_years": 10, "days": 30},     # half block, €12.50
                {"passport_ref": "P3", "full_name": "Baby One",
                 "age_years": 1, "days": 30}])     # free
        self.assertEqual(out["count"], 3)
        self.assertEqual(out["total_minor"], 6250)
        self.assertTrue(all(c["group_ref"] == out["group_ref"]
                            for c in out["covers"]))
        self.assertTrue(all("qr" in c for c in out["covers"]))

    def test_bad_row_refuses_the_whole_manifest(self):
        vhc = _cv()
        with self.assertRaises(SalesError) as ctx:
            purchase_group(
                vhc, payer_ref="TOURCO-1", entry_day=10, day=1,
                key_id="k1",
                travelers=[{"passport_ref": "P1", "full_name": "Ok",
                            "age_years": 35, "days": 30},
                           {"passport_ref": "", "full_name": "Broken",
                            "age_years": 35, "days": 30}])
        self.assertIn("nobody charged", str(ctx.exception))
        self.assertEqual(len(vhc.covers), 0)  # nobody was charged

    def test_empty_manifest_refuses(self):
        with self.assertRaises(SalesError):
            purchase_group(_cv(), payer_ref="T", travelers=[],
                           entry_day=1, day=1, key_id="k1")


class TestPartnerApi(unittest.TestCase):
    def _partner(self):
        partners = PartnerRegistry()
        partners.register("air-cv", legal_name="Air Cabo Verde",
                          api_key="topsecret", day=1)
        return partners

    def test_key_is_hashed_at_rest(self):
        partners = self._partner()
        self.assertNotIn("topsecret", str(partners.partners))

    def test_partner_quote_and_purchase_attributed(self):
        vhc, partners = _cv(), self._partner()
        q = partners.quote_for(vhc, "air-cv", "topsecret", days=30,
                               age_years=35, day=1)
        cover = partners.purchase_for(vhc, "air-cv", "topsecret",
                                      quote_id=q["quote_id"],
                                      passport_ref="PAX-1",
                                      full_name="Pax One",
                                      entry_day=10, day=1, key_id="k1")
        self.assertEqual(cover["partner_id"], "air-cv")
        rep = partners.report()
        self.assertEqual(rep["sales"], 1)
        self.assertEqual(rep["gross_minor"], 2500)

    def test_bad_key_and_revoked_partner_refuse(self):
        vhc, partners = _cv(), self._partner()
        with self.assertRaises(SalesError):
            partners.quote_for(vhc, "air-cv", "wrong", days=30,
                               age_years=35, day=1)
        partners.revoke("air-cv", reason="contract ended", day=5)
        with self.assertRaises(SalesError):
            partners.quote_for(vhc, "air-cv", "topsecret", days=30,
                               age_years=35, day=6)

    def test_quote_binding_across_partners_refuses(self):
        vhc = _cv()
        partners = self._partner()
        partners.register("ota-1", legal_name="OTA One",
                          api_key="otakey", day=1)
        q = partners.quote_for(vhc, "air-cv", "topsecret", days=30,
                               age_years=35, day=1)
        with self.assertRaises(SalesError):
            partners.purchase_for(vhc, "ota-1", "otakey",
                                  quote_id=q["quote_id"],
                                  passport_ref="P", full_name="X",
                                  entry_day=10, day=1, key_id="k1")

    def test_revocation_needs_reason(self):
        partners = self._partner()
        with self.assertRaises(SalesError):
            partners.revoke("air-cv", reason="", day=2)


if __name__ == "__main__":
    unittest.main()
