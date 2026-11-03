import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
from travelpass import (TravelPassRegistry, TravelPassError,  # noqa: E402
                        quote_premium, POLICY, TRAVEL_POLICY_VERSION)

PROFILE = {"blood_group": "O+", "allergies": ["penicillin"],
           "conditions": ["asthma"], "medications": ["salbutamol"],
           "emergency_contacts": [{"name": "Ada", "phone": "+234…"}]}


def make_reg():
    return TravelPassRegistry("ng")


def enroll_paid(reg, tid="T-1", **kw):
    args = dict(full_name="Jane Doe", passport_ref="A1234567",
                nationality="DE", entry_day=0, exit_day=13,
                emergency_profile=dict(PROFILE), day=0)
    args.update(kw)
    reg.enroll(tid, **args)
    q = quote_premium(0, 13)
    reg.pay(tid, amount_kobo=q["premium_kobo"], method="card",
            reference=f"pay-{tid}", day=0)
    return tid


class TestPolicy(unittest.TestCase):
    def test_quote_from_versioned_table(self):
        q = quote_premium(0, 13)
        self.assertEqual(q["premium_kobo"],
                         POLICY["flat_fee_kobo"]
                         + POLICY["per_day_kobo"] * 14)
        self.assertEqual(q["policy_version"], TRAVEL_POLICY_VERSION)

    def test_beyond_tourist_cap_refuses(self):
        with self.assertRaises(TravelPassError):
            quote_premium(0, 200)
        with self.assertRaises(TravelPassError):
            quote_premium(10, 5)


class TestEnrollmentAndPayment(unittest.TestCase):
    def test_enroll_then_full_payment_is_active(self):
        reg = make_reg()
        enroll_paid(reg)
        self.assertEqual(reg.coverage_status("T-1", 5)["status"],
                         "active")

    def test_shortfall_is_stated_not_silent(self):
        reg = make_reg()
        reg.enroll("T-2", full_name="John Roe", passport_ref="B765",
                   nationality="GB", entry_day=0, exit_day=13,
                   emergency_profile={}, day=0)
        due = quote_premium(0, 13)["premium_kobo"]
        reg.pay("T-2", amount_kobo=due - 1, method="card",
                reference="p1", day=0)
        st = reg.coverage_status("T-2", 3)
        self.assertEqual(st["status"], "shortfall")
        self.assertEqual(st["shortfall_kobo"], 1)

    def test_expired_window(self):
        reg = make_reg()
        enroll_paid(reg)
        self.assertEqual(reg.coverage_status("T-1", 99)["status"],
                         "expired")

    def test_payment_gates(self):
        reg = make_reg()
        reg.enroll("T-3", full_name="A B", passport_ref="C1",
                   nationality="US", entry_day=0, exit_day=5,
                   emergency_profile={}, day=0)
        with self.assertRaises(TravelPassError):  # zero
            reg.pay("T-3", amount_kobo=0, method="card",
                    reference="x", day=0)
        with self.assertRaises(TravelPassError):  # unknown method
            reg.pay("T-3", amount_kobo=100, method="bitcoin",
                    reference="x", day=0)
        reg.pay("T-3", amount_kobo=100, method="cash",
                reference="x", day=0)
        with self.assertRaises(TravelPassError):  # reused reference
            reg.pay("T-3", amount_kobo=100, method="card",
                    reference="x", day=0)

    def test_enrollment_gates(self):
        reg = make_reg()
        with self.assertRaises(TravelPassError):
            reg.enroll("T-4", full_name="", passport_ref="X",
                       nationality="US", entry_day=0, exit_day=5,
                       emergency_profile={}, day=0)
        with self.assertRaises(TravelPassError):  # bad blood group
            reg.enroll("T-5", full_name="A", passport_ref="X",
                       nationality="US", entry_day=0, exit_day=5,
                       emergency_profile={"blood_group": "Z+"},
                       day=0)


class TestEmergencyAccess(unittest.TestCase):
    def setUp(self):
        self.reg = make_reg()
        enroll_paid(self.reg)
        self.pass_ = self.reg.issue_pass("T-1", day=0)

    def test_qr_is_a_key_not_the_record(self):
        self.assertIn("er.ng.gov", self.pass_["qr_payload"])
        self.assertNotIn("Jane", self.pass_["token"])
        # registry stores the hash, not the token
        import hashlib
        h = hashlib.sha256(self.pass_["token"].encode()).hexdigest()
        self.assertIn(h, self.reg.tokens)

    def test_break_glass_returns_emergency_subset_only(self):
        card = self.reg.emergency_access(
            self.pass_["token"], provider_id="para-1",
            facility="er-lagos", reason="rta-unresponsive", day=2)
        self.assertEqual(card["blood_group"], "O+")
        self.assertEqual(card["allergies"], ["penicillin"])
        self.assertEqual(card["coverage"]["status"], "active")
        self.assertNotIn("assessed_premium_kobo", card)
        self.assertNotIn("passport_ref", card)
        self.assertEqual(len(self.reg.access_log), 1)

    def test_unknown_revoked_and_unattributed_tokens_refuse(self):
        with self.assertRaises(TravelPassError):
            self.reg.emergency_access("tp_ghost", provider_id="p",
                                      facility="f", reason="r", day=1)
        with self.assertRaises(TravelPassError):  # no reason
            self.reg.emergency_access(self.pass_["token"],
                                      provider_id="p", facility="f",
                                      reason="", day=1)
        self.reg.revoke_pass(self.pass_["token"], day=3, by="admin-1",
                             reason="reported lost")
        with self.assertRaises(TravelPassError):
            self.reg.emergency_access(self.pass_["token"],
                                      provider_id="p", facility="f",
                                      reason="r", day=3)

    def test_lapsed_coverage_opens_record_but_flags_money(self):
        card = self.reg.emergency_access(
            self.pass_["token"], provider_id="para-1",
            facility="er-kano", reason="collapse", day=99)
        self.assertEqual(card["coverage"]["status"], "expired")
        self.assertEqual(card["blood_group"], "O+")  # airway first
        ev = self.reg.record_emergency(
            "T-1", session_id=card["session_id"], day=99,
            facility="er-kano", summary="anaphylaxis, treated")
        self.assertFalse(ev["payable"])

    def test_absence_stated_not_blanked(self):
        self.reg.enroll("T-6", full_name="No Hist", passport_ref="Z9",
                        nationality="FR", entry_day=0, exit_day=5,
                        emergency_profile={}, day=0)
        p = self.reg.issue_pass("T-6", day=0)
        card = self.reg.emergency_access(p["token"], provider_id="x",
                                         facility="f", reason="r",
                                         day=1)
        self.assertEqual(card["blood_group"], "not-recorded")
        self.assertEqual(card["allergies"], "none-recorded")

    def test_event_binds_to_its_own_session(self):
        card = self.reg.emergency_access(
            self.pass_["token"], provider_id="para-1",
            facility="er-lagos", reason="rta", day=2)
        with self.assertRaises(TravelPassError):
            self.reg.record_emergency("T-1", session_id="ER-999",
                                      day=2, facility="f",
                                      summary="x")
        ev = self.reg.record_emergency(
            "T-1", session_id=card["session_id"], day=2,
            facility="er-lagos", summary="fracture stabilised")
        self.assertTrue(ev["payable"])

    def test_report_ships_denominators(self):
        self.reg.emergency_access(self.pass_["token"],
                                  provider_id="p", facility="f",
                                  reason="r", day=2)
        rep = self.reg.report(as_of=2)
        self.assertEqual(rep["travelers_enrolled"], 1)
        self.assertEqual(rep["coverage"]["active"], 1)
        self.assertEqual(rep["emergency_accesses"], 1)
        self.assertGreater(rep["premium_collected_kobo"], 0)


if __name__ == "__main__":
    unittest.main()
