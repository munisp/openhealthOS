"""Tests for vhcover — the country-agnostic visitor health cover engine:
block vs flat-per-day pricing, age bands, signed offline QR, the
green/amber/red verdict matrix, contiguous extensions, and offline
caches that know when they are stale."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhcover import (KeyRing, VHCError, VHCCoverRegistry,  # noqa: E402
                     display_amount, price_cover, read_qr,
                     VHC_PROFILES)

CV = "cv-vhc@1.0.0"
NG = "ng-vhc@1.0.0"


def _ring():
    ring = KeyRing()
    ring.register("k1", "secret-one", active_from=0)
    return ring


def _cv():
    return VHCCoverRegistry(profile_version=CV, key_ring=_ring(),
                            issuer="cv-vhc")


def _buy(reg, *, days=40, age=35, entry_day=10, day=10):
    q = reg.quote(days=days, age_years=age, day=day)
    return reg.purchase(quote_id=q["quote_id"], passport_ref="P12345",
                        full_name="Ada Lovelace", entry_day=entry_day,
                        day=day, key_id="k1")


class TestPricing(unittest.TestCase):
    def test_cv_blocks_round_up(self):
        p = VHC_PROFILES[CV]
        self.assertEqual(price_cover(p, days=40, age_years=35), (5000, 2))
        self.assertEqual(price_cover(p, days=30, age_years=35), (2500, 1))

    def test_cv_age_bands(self):
        p = VHC_PROFILES[CV]
        self.assertEqual(price_cover(p, days=30, age_years=10), (1250, 1))
        self.assertEqual(price_cover(p, days=30, age_years=1), (0, 1))

    def test_ng_flat_per_day(self):
        p = VHC_PROFILES[NG]
        self.assertEqual(price_cover(p, days=7, age_years=35),
                         (500_000 + 7 * 50_000, 7))

    def test_ng_tourism_cap(self):
        with self.assertRaises(VHCError):
            price_cover(VHC_PROFILES[NG], days=91, age_years=35)

    def test_age_is_mandatory(self):
        with self.assertRaises(VHCError):
            price_cover(VHC_PROFILES[CV], days=30, age_years=None)

    def test_cve_display_is_a_courtesy(self):
        shown = display_amount(VHC_PROFILES[CV], 2500)
        self.assertEqual(shown["major"], 25.0)
        self.assertEqual(shown["alt"]["currency"], "CVE")
        self.assertAlmostEqual(shown["alt"]["major"], 2756.62, places=2)


class TestSales(unittest.TestCase):
    def test_quote_expires(self):
        reg = _cv()
        q = reg.quote(days=30, age_years=35, day=1)
        with self.assertRaises(VHCError):
            reg.purchase(quote_id=q["quote_id"], passport_ref="P",
                         full_name="N", entry_day=5, day=3, key_id="k1")

    def test_purchase_issues_signed_qr_and_hashes_passport(self):
        reg = _cv()
        cover = _buy(reg)
        self.assertEqual(len(cover["qr"].split(".")), 3)
        self.assertNotIn("P12345", cover["qr"])
        claims, ok, _ = read_qr(cover["qr"], key_ring=_ring(), day=10)
        self.assertTrue(ok)
        self.assertEqual(claims["win"], {"from": 10, "to": 49})
        self.assertEqual(claims["vu"], 100)  # 90-day offline validity

    def test_free_cover_still_issued_and_counted(self):
        reg = _cv()
        cover = _buy(reg, days=30, age=1)
        self.assertEqual(reg.payments[-1]["kind"], "exempt-issue")
        self.assertEqual(reg.report()["exempt_covers"], 1)
        self.assertIn("qr", cover)

    def test_unknown_profile_refuses(self):
        with self.assertRaises(VHCError):
            VHCCoverRegistry(profile_version="xx@9", key_ring=_ring(),
                             issuer="x")


class TestVerdicts(unittest.TestCase):
    def test_green_online(self):
        reg = _cv()
        cover = _buy(reg)
        v = reg.verify(cover["qr"], day=20)
        self.assertEqual(v["verdict"], "green")
        self.assertTrue(v["evidence"]["within_budget"])
        self.assertEqual(v["officer_action"], "admit")

    def test_tampered_qr_is_red_not_a_crash(self):
        reg = _cv()
        cover = _buy(reg)
        # Flip a fully-significant signature character. (Tail tampers on
        # an unpadded base64url segment are caught even earlier — as
        # "non-canonical encoding" — because the final char carries
        # spare bits; see test_vhtamper.py.)
        # Pin the flip PROVABLY inside the signature segment — the
        # one observed flake stays stated, and the position is no
        # longer relative to total length (ADR-0079).
        sig_start = cover["qr"].rindex(".") + 1
        self.assertGreaterEqual(len(cover["qr"]) - sig_start, 4)
        pos = sig_start + 2
        bad = (cover["qr"][:pos]
               + ("x" if cover["qr"][pos] != "x" else "y")
               + cover["qr"][pos + 1:])
        v = reg.verify(bad, day=20)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("unverifiable: bad signature", v["reasons"])

    def test_unknown_key_is_red(self):
        ring = KeyRing()
        ring.register("k9", "other", active_from=0)
        other = VHCCoverRegistry(profile_version=CV, key_ring=ring,
                                 issuer="cv-vhc")
        q = other.quote(days=40, age_years=35, day=10)
        cover = other.purchase(quote_id=q["quote_id"],
                               passport_ref="P12345",
                               full_name="Ada Lovelace", entry_day=10,
                               day=10, key_id="k9")
        v = _cv().verify(cover["qr"], day=20)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("unverifiable: unknown key", v["reasons"])

    def test_revoked_is_red(self):
        reg = _cv()
        cover = _buy(reg)
        reg.revoke(cover["ref"], reason="duplicate issue", day=21)
        v = reg.verify(cover["qr"], day=22)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("revoked", v["reasons"])

    def test_grace_then_lapsed(self):
        reg = _cv()
        cover = _buy(reg, days=10, entry_day=10)  # window to day 19
        self.assertEqual(reg.verify(cover["qr"], day=20)["verdict"],
                         "amber")   # grace
        v = reg.verify(cover["qr"], day=21)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("lapsed", v["reasons"])

    def test_expiring_soon_is_amber(self):
        reg = _cv()
        cover = _buy(reg, days=10, entry_day=10)
        v = reg.verify(cover["qr"], day=17)  # 2 days left ≤ 3
        self.assertEqual(v["verdict"], "amber")
        self.assertIn("cover expires within 72h", v["reasons"])

    def test_humanitarian_flag_is_amber_then_expires(self):
        reg = _cv()
        cover = _buy(reg, days=40)
        reg.humanitarian_flag(cover["ref"], day=20, note="irregular entry")
        v = reg.verify(cover["qr"], day=22)
        self.assertEqual(v["verdict"], "amber")
        self.assertIn("humanitarian regularisation pending", v["reasons"])
        self.assertEqual(reg.verify(cover["qr"], day=30)["verdict"],
                         "green")

    def test_revocation_without_reason_refuses(self):
        reg = _cv()
        cover = _buy(reg)
        with self.assertRaises(VHCError):
            reg.revoke(cover["ref"], reason="", day=1)


class TestOffline(unittest.TestCase):
    def test_offline_cache_verifies_green(self):
        reg = _cv()
        cover = _buy(reg)
        cache = reg.offline_cache(as_of=15)
        v = reg.verify(cover["qr"], day=16, cache=cache)
        self.assertEqual(v["verdict"], "green")
        self.assertEqual(v["evidence"]["mode"], "offline")
        self.assertEqual(v["evidence"]["cache_age_days"], 1)

    def test_stale_cache_is_red(self):
        reg = _cv()
        cover = _buy(reg)
        cache = reg.offline_cache(as_of=10)
        v = reg.verify(cover["qr"], day=14, cache=cache)  # age 4 > 3
        self.assertEqual(v["verdict"], "red")
        self.assertTrue(any(r.startswith("verification data stale")
                            for r in v["reasons"]))

    def test_offline_sees_revocations_from_snapshot(self):
        reg = _cv()
        cover = _buy(reg)
        reg.revoke(cover["ref"], reason="fraud", day=12)
        cache = reg.offline_cache(as_of=12)
        self.assertEqual(reg.verify(cover["qr"], day=12,
                                    cache=cache)["verdict"], "red")

    def test_key_rotation_old_tokens_still_verify_in_window(self):
        ring = KeyRing()
        ring.register("k1", "one", active_from=0, active_to=50)
        ring.register("k2", "two", active_from=51)
        reg = VHCCoverRegistry(profile_version=CV, key_ring=ring,
                               issuer="cv-vhc")
        cover = _buy(reg, day=10)
        self.assertEqual(reg.verify(cover["qr"], day=60)["verdict"],
                         "red")  # k1 retired, token unverifiable
        claims, ok, _ = read_qr(cover["qr"], key_ring=ring, day=40)
        self.assertTrue(ok)


class TestExtensions(unittest.TestCase):
    def test_extension_is_contiguous_and_repriced(self):
        reg = _cv()
        cover = _buy(reg, days=30, entry_day=10)  # to 39
        reg.extend(cover["ref"], extra_days=20, day=35, key_id="k1")
        self.assertEqual(cover["window"]["to"], 69)  # +1 block of 30
        self.assertEqual(reg.payments[-1]["minor"], 1000)
        claims, ok, _ = read_qr(cover["qr"], key_ring=reg.key_ring,
                                day=36)
        self.assertTrue(ok)
        self.assertEqual(claims["win"]["to"], 69)

    def test_extension_after_grace_refuses(self):
        reg = _cv()
        cover = _buy(reg, days=10, entry_day=10)  # to 19, grace to 20
        with self.assertRaises(VHCError):
            reg.extend(cover["ref"], extra_days=30, day=21, key_id="k1")

    def test_ng_extension_is_per_day(self):
        reg = VHCCoverRegistry(profile_version=NG, key_ring=_ring(),
                               issuer="ng-vhc")
        cover = _buy(reg, days=5, entry_day=10)
        reg.extend(cover["ref"], extra_days=3, day=12, key_id="k1")
        self.assertEqual(cover["window"]["to"], 17)
        self.assertEqual(reg.payments[-1]["minor"], 150_000)


class TestReport(unittest.TestCase):
    def test_report_shows_denominators(self):
        reg = _cv()
        _buy(reg, days=40)
        _buy(reg, days=30, age=1)
        rep = reg.report()
        self.assertEqual(rep["covers"], 2)
        self.assertEqual(rep["collected_minor"], 5000)
        self.assertEqual(rep["currency"], "EUR")


if __name__ == "__main__":
    unittest.main()


class TestExemptions(unittest.TestCase):
    def _exempt(self, reg, **kw):
        args = dict(passport_ref="DIP-1", full_name="Diplo Matic",
                    category="diplomatic", evidence_ref="MFA-CARD-77",
                    day=10, expires_day=40, key_id="k1", dob="1970-01-01")
        args.update(kw)
        return reg.register_exemption(**args)

    def test_diplomatic_exemption_verifies_green(self):
        reg = _cv()
        ex = self._exempt(reg)
        v = reg.verify(ex["qr"], day=20)
        self.assertEqual(v["verdict"], "green")
        self.assertEqual(v["claims"]["cat"], "diplomatic")

    def test_exemption_needs_evidence(self):
        reg = _cv()
        with self.assertRaises(VHCError):
            self._exempt(reg, evidence_ref="")

    def test_unknown_category_refuses(self):
        reg = _cv()
        with self.assertRaises(VHCError):
            self._exempt(reg, category="vip")

    def test_foreign_policy_floor_enforced(self):
        reg = _cv()
        with self.assertRaises(VHCError):
            self._exempt(reg, category="foreign_policy",
                         foreign_cover_minor=4_999_999)
        ex = self._exempt(reg, category="foreign_policy",
                          foreign_cover_minor=5_000_000)
        self.assertEqual(reg.verify(ex["qr"], day=11)["verdict"],
                         "green")

    def test_transit_window_enforced(self):
        reg = _cv()
        with self.assertRaises(VHCError):
            self._exempt(reg, category="transit", expires_day=13)

    def test_expired_exemption_is_red(self):
        reg = _cv()
        ex = self._exempt(reg)
        v = reg.verify(ex["qr"], day=41)
        self.assertEqual(v["verdict"], "red")
        self.assertIn("exemption expired", v["reasons"])

    def test_exemption_revocation_is_red_and_offline(self):
        reg = _cv()
        ex = self._exempt(reg)
        reg.revoke(ex["ref"], reason="evidence withdrawn", day=15)
        cache = reg.offline_cache(as_of=15)
        self.assertEqual(reg.verify(ex["qr"], day=16,
                                    cache=cache)["verdict"], "red")


class TestDeskService(unittest.TestCase):
    def test_lookup_finds_cover_and_exemption(self):
        reg = _cv()
        q = reg.quote(days=30, age_years=35, day=1)
        reg.purchase(quote_id=q["quote_id"], passport_ref="P-77",
                     full_name="Desk Test", entry_day=10, day=1,
                     key_id="k1", dob="1990-05-05")
        reg.register_exemption(passport_ref="P-77", full_name="Desk Test",
                               category="resident",
                               evidence_ref="RES-PERMIT-1", day=1,
                               expires_day=365, key_id="k1",
                               dob="1990-05-05")
        found = reg.desk_lookup(passport_ref="P-77", dob="1990-05-05")
        self.assertEqual(found["count"], 2)
        kinds = {m["kind"] for m in found["matches"]}
        self.assertEqual(kinds, {"cover", "exemption"})

    def test_zero_matches_is_a_finding_not_an_error(self):
        reg = _cv()
        found = reg.desk_lookup(passport_ref="NOPE-1")
        self.assertEqual(found["count"], 0)
        self.assertIn("not met", found["note"])

    def test_reissue_returns_fresh_signed_qr(self):
        reg = _cv()
        cover = _buy(reg)
        qr2 = reg.reissue(cover["ref"], day=20, key_id="k1")
        self.assertEqual(reg.verify(qr2, day=20)["verdict"], "green")
        self.assertEqual(reg.audit[-1]["event"], "reissued")

    def test_reissue_unknown_ref_refuses(self):
        reg = _cv()
        with self.assertRaises(VHCError):
            reg.reissue("VHC-NOPE", day=1, key_id="k1")
