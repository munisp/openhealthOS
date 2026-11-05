"""Tests for vhclearance + engine extensions — batch manifest
pre-clearance, S-009 corrections, S-020 refunds, FR-VER-06 photo-hash."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhclearance import ClearanceError, preclear_manifest  # noqa: E402
from vhcover import KeyRing, VHCError, VHCCoverRegistry  # noqa: E402

CV = "cv-vhc@1.0.0"


def _cv():
    ring = KeyRing()
    ring.register("k1", "secret-one", active_from=0)
    return VHCCoverRegistry(profile_version=CV, key_ring=ring,
                            issuer="cv-vhc")


def _buy(reg, *, days=40, age=35, entry_day=10, day=1, passport="P-1",
         dob="1985-03-03"):
    q = reg.quote(days=days, age_years=age, day=day)
    return reg.purchase(quote_id=q["quote_id"], passport_ref=passport,
                        full_name="Ada Lovelace", entry_day=entry_day,
                        day=day, key_id="k1", dob=dob)


class TestPreclearance(unittest.TestCase):
    def test_manifest_statuses_and_checksum(self):
        reg = _cv()
        _buy(reg, passport="P-GOOD")
        reg.register_exemption(passport_ref="P-DIP", full_name="Dip Lo",
                               category="diplomatic",
                               evidence_ref="MFA-1", day=1,
                               expires_day=99, key_id="k1")
        q = reg.quote(days=5, age_years=35, day=1)   # expires soon
        reg.purchase(quote_id=q["quote_id"], passport_ref="P-SHORT",
                     full_name="Short Stay", entry_day=10, day=1,
                     key_id="k1")
        out = preclear_manifest(
            reg, flight_ref="TACV-201", travel_day=12, submitted_day=8,
            rows=[{"passport_ref": "P-GOOD", "dob": "1985-03-03"},
                  {"passport_ref": "P-DIP"},
                  {"passport_ref": "P-SHORT"},
                  {"passport_ref": "P-NOBODY"}])
        statuses = [r["status"] for r in out["rows"]]
        self.assertEqual(statuses, ["cleared", "cleared-exempt",
                                    "review", "not-cleared"])
        self.assertEqual(out["counts"]["total"], 4)
        self.assertEqual(len(out["manifest_sha256"]), 64)
        self.assertNotIn("Ada", str(out))  # names never ride back

    def test_empty_manifest_and_missing_flight_refuse(self):
        reg = _cv()
        with self.assertRaises(ClearanceError):
            preclear_manifest(reg, flight_ref="", rows=[{}],
                              travel_day=1, submitted_day=1)
        with self.assertRaises(ClearanceError):
            preclear_manifest(reg, flight_ref="F", rows=[],
                              travel_day=1, submitted_day=1)

    def test_lapsed_cover_is_review_not_cleared(self):
        reg = _cv()
        _buy(reg, days=5, entry_day=10)  # to day 14
        out = preclear_manifest(
            reg, flight_ref="F", travel_day=20, submitted_day=15,
            rows=[{"passport_ref": "P-1", "dob": "1985-03-03"}])
        self.assertEqual(out["rows"][0]["status"], "review")
        self.assertIn("lapsed", out["rows"][0]["reason"])


class TestCorrections(unittest.TestCase):
    def test_one_free_correction_then_four_eyes(self):
        reg = _cv()
        cover = _buy(reg)
        reg.correct_cover(cover["ref"], new_name="Ada Lovelace-Byron",
                          by="desk-1", day=2, key_id="k1")
        with self.assertRaises(VHCError):
            reg.correct_cover(cover["ref"], new_name="A. Lovelace",
                              by="desk-1", day=3, key_id="k1")
        reg.correct_cover(cover["ref"], new_name="A. Lovelace",
                          by="desk-1", day=3, key_id="k1",
                          supervisor="sup-1")
        claims, ok, _ = __import__("vhcover").read_qr(
            cover["qr"], key_ring=reg.key_ring, day=3)
        self.assertTrue(ok)
        self.assertEqual(claims["name"], "A. Lovelace")


class TestRefunds(unittest.TestCase):
    def test_full_refund_before_window(self):
        reg = _cv()
        cover = _buy(reg, entry_day=10)
        r = reg.refund_cover(cover["ref"], reason="pre-travel", day=5)
        self.assertEqual(r["refund_minor"], 5000)
        self.assertIn("never opened", r["basis"])

    def test_prorata_over_unused_full_blocks(self):
        reg = _cv()
        cover = _buy(reg, days=40, entry_day=10)  # 2 blocks, €50
        r = reg.refund_cover(cover["ref"], reason="early-exit", day=19)
        self.assertEqual(r["refund_minor"], 2500)  # 31 days remain: one
        # full 30-day block refunds; partial blocks never do
        self.assertIn("unused full 30-day blocks", r["basis"])

    def test_visa_refusal_needs_evidence(self):
        reg = _cv()
        cover = _buy(reg)
        with self.assertRaises(VHCError):
            reg.refund_cover(cover["ref"], reason="visa-refusal", day=2)
        r = reg.refund_cover(cover["ref"], reason="visa-refusal", day=2,
                             evidence_ref="EMB-REFUSAL-1")
        self.assertEqual(r["refund_minor"], 5000)
        self.assertEqual(r["sla_business_days"], 10)

    def test_double_refund_refuses(self):
        reg = _cv()
        cover = _buy(reg)
        reg.refund_cover(cover["ref"], reason="pre-travel", day=2)
        with self.assertRaises(VHCError):
            reg.refund_cover(cover["ref"], reason="pre-travel", day=3)


class TestPhotoHash(unittest.TestCase):
    def test_photo_mismatch_is_amber_to_desk(self):
        reg = _cv()
        q = reg.quote(days=40, age_years=35, day=1)
        cover = reg.purchase(quote_id=q["quote_id"], passport_ref="P",
                             full_name="N", entry_day=10, day=1,
                             key_id="k1",
                             photo_sha256="a" * 64)
        v = reg.verify(cover["qr"], day=20,
                       presented_photo_sha256="b" * 64)
        self.assertEqual(v["verdict"], "amber")
        self.assertIn("photo mismatch — to the desk", v["reasons"])
        ok = reg.verify(cover["qr"], day=20,
                        presented_photo_sha256="a" * 64)
        self.assertEqual(ok["verdict"], "green")


if __name__ == "__main__":
    unittest.main()
