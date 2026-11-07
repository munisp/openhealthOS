"""Growth-innovation tests — sponsor cover, USSD/WhatsApp lane,
parametric rider, ECOWAS roam, campus cohorts, premium financing."""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhcover import KeyRing, VHCCoverRegistry
from vhsponsor import (SponsorBook, SponsorError, fx_quote,
                       whatsapp_deeplink)
from vhchannel import ChannelError, LaneSession, verify_by_code
from vhrider import (ORACLES, RiderEngine, RiderError, payout_for_delay,
                     register_oracle)
from vhroam import (ECOWAS_MEMBERS, RoamError, compose_roam_profile,
                    ecowas_roam, price_roam, verdict_rule)
from vhcohorts import CohortError, CohortRegistry
from vhcredit import CreditBook, CreditError


def _vhc(profile="ng-vhc@1.0.0"):
    kr = KeyRing()
    kr.register("k1", "secret", active_from=0, active_to=10_000)
    return VHCCoverRegistry(profile_version=profile, key_ring=kr,
                            issuer="test"), kr


class TestSponsor(unittest.TestCase):
    def test_fx_quote_uses_stated_rates(self):
        q = fx_quote(from_currency="USD", to_currency="NGN",
                     amount_minor=10000, day=0)
        self.assertEqual(q["to"], "NGN")
        self.assertGreater(q["amount_minor"], 0)
        same = fx_quote(from_currency="EUR", to_currency="EUR",
                        amount_minor=500, day=0)
        self.assertEqual(same["amount_minor"], 500)

    def test_unknown_pair_refuses(self):
        with self.assertRaises(SponsorError):
            fx_quote(from_currency="GBP", to_currency="NGN",
                     amount_minor=100, day=0)   # GBP: valid currency, no stated rate

    def test_sponsor_cover_names_both_parties(self):
        vhc, _ = _vhc()
        book = SponsorBook()
        rec = book.sponsor_cover(
            vhc, sponsor_ref="diaspora-us-01", sponsor_currency="USD",
            traveller_passport="NG-A12345", traveller_name="Ada O",
            days=14, age_years=30, day=0, key_id="k1")
        self.assertEqual(rec["premium_minor"], 1200000)
        # the sponsor pays in their own currency: the quote converts
        # the NGN premium INTO USD, never the other way round
        q = rec["sponsor_quote"]
        self.assertEqual(q["from"], "NGN")
        self.assertEqual(q["to"], "USD")
        from vhtreasury import RATES
        eur = 1200000 / 100 / RATES["NGN"]["per_eur"]
        self.assertEqual(q["amount_minor"],
                         int(round(eur * RATES["USD"]["per_eur"] * 100)))
        self.assertTrue(rec["ref"].startswith("SPN-"))

    def test_whatsapp_link_is_tls(self):
        link = whatsapp_deeplink(pass_url="https://er.ng.gov/er#t=abc",
                                 text="Your cover")
        self.assertTrue(link.startswith("https://wa.me/"))
        with self.assertRaises(SponsorError):
            whatsapp_deeplink(pass_url="http://x", text="t")


class TestChannel(unittest.TestCase):
    def test_unregistered_telco_refuses(self):
        with self.assertRaises(ChannelError):
            LaneSession("s1", telco="unknown-telco", country="ng", day=0)
        with self.assertRaises(Exception):
            LaneSession("s2", telco="mtn-ng", country="xx", day=0)

    def test_buy_flow_walks_the_states(self):
        s = LaneSession("s1", telco="mtn-ng", country="ng", day=0)
        self.assertIn("Buy cover", s.handle(""))
        self.assertIn("Days", s.handle("1"))
        self.assertIn("age", s.handle("14").lower())
        self.assertIn("Confirm", s.handle("30"))
        self.assertIn("Quote issued", s.handle("1"))
        self.assertEqual(s.draft["days"], 14)

    def test_age_required_on_the_lane_too(self):
        s = LaneSession("s1", telco="mtn-ng", country="ng", day=0)
        s.handle("")
        s.handle("1")
        s.handle("7")
        with self.assertRaises(ChannelError):
            s.handle("")   # age blank

    def test_illegal_transition_is_a_fraud_shape(self):
        s = LaneSession("s1", telco="mtn-ng", country="ng", day=0)
        s.state = "buy_confirm"
        with self.assertRaises(ChannelError):
            s._go("menu")

    def test_verify_by_code_red_on_unknown(self):
        class NoReg:
            def verify(self, **kw):
                raise ValueError("no")
        v = verify_by_code(NoReg(), cover_ref="VHC-X", day=0, lane=3)
        self.assertEqual(v["verdict"], "red")


class TestRider(unittest.TestCase):
    def setUp(self):
        self.kr = KeyRing()
        self.kr.register("oracle-1", "secret", active_from=0,
                         active_to=10_000)
        register_oracle("skyfeed", signature_key_id="oracle-1",
                        source="flight-status-feed")
        self.eng = RiderEngine(key_ring=self.kr)

    def _attest(self, delay=300):
        payload = f"LOS-LHR|delayed|{delay}|0".encode()
        import hmac, hashlib
        # ring custody keys verify with the raw secret — HMAC path
        sig = hmac.new(b"secret", payload, hashlib.sha256).hexdigest()
        return self.eng.attest("skyfeed", flight="LOS-LHR",
                               disruption="delayed",
                               delay_minutes=delay,
                               signature=sig.encode(), day=0)

    def test_payout_table(self):
        self.assertEqual(payout_for_delay(300, unit_price_minor=2500),
                         2500)
        self.assertEqual(payout_for_delay(130, unit_price_minor=2500),
                         1250)
        self.assertEqual(payout_for_delay(30, unit_price_minor=2500), 0)

    def test_unknown_oracle_refuses(self):
        with self.assertRaises(RiderError):
            self.eng.attest("nope", flight="F", disruption="d",
                            delay_minutes=1, signature=b"s", day=0)

    def test_trigger_without_attestation_refuses(self):
        vhc, _ = _vhc()
        with self.assertRaises(RiderError):
            self.eng.trigger(vhc, cover_ref="VHC-X",
                             event_sha256="0" * 64, day=0)


class TestRoam(unittest.TestCase):
    def test_ecowas_membership_is_data(self):
        self.assertEqual(len(ECOWAS_MEMBERS), 15)
        self.assertIn("cv", ECOWAS_MEMBERS)     # the archipelago is in
        self.assertIn("ng", ECOWAS_MEMBERS)

    def test_compose_requires_profile_entries(self):
        with self.assertRaises(RoamError):
            compose_roam_profile("roam-x", countries=("atlantis",),
                                 label="x")

    def test_price_roam_segments_per_profile(self):
        roam = compose_roam_profile("roam-ngcv", countries=("ng", "cv"),
                                    label="NG+CV")
        out = price_roam(roam, segments={"ng": 14, "cv": 14},
                         age_years=30)
        self.assertTrue(out["mixed_currency"])
        self.assertEqual(out["totals_by_currency"]["NGN"], 1200000)
        self.assertEqual(out["totals_by_currency"]["EUR"], 2500)
        self.assertEqual(len(out["lines"]), 2)

    def test_template_member_refuses(self):
        roam = compose_roam_profile("roam-gh", countries=("gh",),
                                    label="GH")
        with self.assertRaises(Exception):
            price_roam(roam, segments={"gh": 5}, age_years=30)

    def test_strictest_verdict_wins(self):
        roam = ecowas_roam()
        self.assertEqual(verdict_rule(roam, ["green", "amber"]), "amber")
        self.assertEqual(verdict_rule(roam, ["green", "red"]), "red")
        self.assertEqual(verdict_rule(roam, ["green"]), "green")
        with self.assertRaises(RoamError):
            verdict_rule(roam, [])


class TestCohorts(unittest.TestCase):
    def setUp(self):
        self.reg = CohortRegistry()
        self.reg.create("unilag-intl-2026", institution="Unilag",
                        country="ng", term_days=120,
                        price_per_member_minor=5000000, currency="NGN",
                        ratified_by="bursar", approved_by="vc", day=0,
                        campus_provider_ref="unilag-clinic")

    def test_four_eyes_and_invoice(self):
        with self.assertRaises(CohortError):
            self.reg.create("c2", institution="X", country="ng",
                            term_days=10, price_per_member_minor=1,
                            currency="NGN", ratified_by="a",
                            approved_by="a", day=0)
        self.reg.add_member("unilag-intl-2026", member_ref="MAT-001",
                            full_name="Ada", day=0)
        self.reg.add_member("unilag-intl-2026", member_ref="MAT-002",
                            full_name="Bola", day=0)
        inv = self.reg.invoice("unilag-intl-2026")
        self.assertEqual(inv["total_minor"], 10000000)
        self.assertEqual(len(inv["invoice_sha256"]), 64)

    def test_not_on_manifest_not_covered(self):
        self.reg.add_member("unilag-intl-2026", member_ref="MAT-001",
                            full_name="Ada", day=0)
        self.assertTrue(self.reg.is_covered("unilag-intl-2026",
                                            "MAT-001"))
        self.assertFalse(self.reg.is_covered("unilag-intl-2026",
                                             "MAT-999"))

    def test_census_freezes_roster(self):
        self.reg.add_member("unilag-intl-2026", member_ref="MAT-001",
                            full_name="Ada", day=0)
        self.reg.close_census("unilag-intl-2026", day=1, by="registrar")
        with self.assertRaises(CohortError):
            self.reg.add_member("unilag-intl-2026", member_ref="MAT-002",
                                full_name="Bola", day=1)


class TestCredit(unittest.TestCase):
    def _plan(self, book):
        return book.open_plan("p1", cover_ref="VHC-1",
                              total_minor=1200000, per_day_minor=50000,
                              installments=4, window_days=14,
                              currency="NGN", day=0)

    def test_schedule_sums_and_remainder_rides_last(self):
        book = CreditBook()
        p = self._plan(book)
        sched = p.schedule()
        self.assertEqual(sum(sched), 1200000)
        self.assertEqual(len(sched), 4)

    def test_vesting_is_arithmetic(self):
        book = CreditBook()
        p = self._plan(book)
        p.pay(300000, day=0, ref="r1")          # 6 vested days
        st = p.status(3)
        self.assertEqual(st["vested_days"], 6)
        self.assertTrue(st["covered_today"])
        self.assertFalse(p.status(10)["covered_today"])

    def test_default_pauses_at_stated_boundary(self):
        book = CreditBook()
        p = self._plan(book)
        p.pay(300000, day=0, ref="r1")
        st = p.check_default(day=8, due_minor=600000)
        self.assertEqual(st["state"], "paused")
        self.assertEqual(st["paused_at_day"], 6)
        p.pay(300000, day=9, ref="r2")          # catch up
        self.assertEqual(p.status(9)["state"], "vesting")

    def test_overpayment_and_zero_refuse(self):
        book = CreditBook()
        p = self._plan(book)
        with self.assertRaises(CreditError):
            p.pay(1200001, day=0, ref="r")
        with self.assertRaises(CreditError):
            p.pay(0, day=0, ref="r")
        with self.assertRaises(CreditError):
            p.pay(1, day=0, ref="")

    def test_one_payment_is_not_a_plan(self):
        book = CreditBook()
        with self.assertRaises(CreditError):
            book.open_plan("p2", cover_ref="VHC-2", total_minor=100,
                           per_day_minor=10, installments=1,
                           window_days=10, currency="NGN", day=0)


if __name__ == "__main__":
    unittest.main()
