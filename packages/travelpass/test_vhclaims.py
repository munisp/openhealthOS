"""Tests for vhclaims — intake, GOP SLA clock, dossier completeness,
tariff adjudication with glosa and signed overrides, payment runs with
remittance, appeals, and the deterministic fraud sample."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhclaims import (ClaimsError, ClaimsRegistry,  # noqa: E402
                      tariff_for)


def _reg():
    return ClaimsRegistry(rules_version="vhc-claims@1.0.0",
                          tariff_set="cv-tariffs")


def _to_dossier(reg, *, case_id=None, intake_h=100, gop_h=101, day=10):
    case = reg.intake(channel="phone", cover_ref="VHC-1",
                      claimant_name="Ada", summary="laceration, ED",
                      now_h=intake_h, day=day)
    reg.issue_gop(case["case_id"], provider_ref="HOSP-1",
                  limit_minor=100_000, now_h=gop_h)
    reg.submit_dossier(
        case["case_id"], icd10_codes=["S01.0"],
        acts=[{"code": "CONS-ED", "charged_minor": 7000},
              {"code": "SUTURE-MINOR", "charged_minor": 15000}],
        discharge_ref="DIS-1", day=day + 1)
    return case["case_id"]


class TestIntakeAndGop(unittest.TestCase):
    def test_intake_channel_must_be_on_the_door_list(self):
        with self.assertRaises(ClaimsError):
            _reg().intake(channel="pigeon", cover_ref="V", claimant_name="A",
                          summary="s", now_h=0, day=1)

    def test_gop_within_sla_is_clean(self):
        reg = _reg()
        case = reg.intake(channel="whatsapp", cover_ref="VHC-1",
                          claimant_name="Ada", summary="fever",
                          now_h=100, day=1)
        gop = reg.issue_gop(case["case_id"], provider_ref="HOSP-1",
                            limit_minor=50_000, now_h=102)
        self.assertFalse(gop["sla_breach"])
        self.assertEqual(gop["elapsed_hours"], 2)

    def test_late_gop_is_a_named_breach_not_an_excuse(self):
        reg = _reg()
        case = reg.intake(channel="phone", cover_ref="VHC-1",
                          claimant_name="Ada", summary="fever",
                          now_h=100, day=1)
        gop = reg.issue_gop(case["case_id"], provider_ref="HOSP-1",
                            limit_minor=50_000, now_h=105)
        self.assertTrue(gop["sla_breach"])
        self.assertEqual(reg.cases[case["case_id"]]["breaches"][0]
                         ["clock"], "gop")

    def test_second_gop_refuses(self):
        reg = _reg()
        cid = _to_dossier(reg)
        with self.assertRaises(ClaimsError):
            reg.issue_gop(cid, provider_ref="HOSP-2", limit_minor=1,
                          now_h=103)


class TestDossier(unittest.TestCase):
    def test_incomplete_dossiers_refuse(self):
        reg = _reg()
        case = reg.intake(channel="kiosk", cover_ref="VHC-1",
                          claimant_name="Ada", summary="s", now_h=0,
                          day=1)
        reg.issue_gop(case["case_id"], provider_ref="H", limit_minor=1,
                      now_h=1)
        with self.assertRaises(ClaimsError):
            reg.submit_dossier(case["case_id"], icd10_codes=[],
                               acts=[{"code": "CONS-ED",
                                      "charged_minor": 1}],
                               discharge_ref="D", day=2)
        with self.assertRaises(ClaimsError):
            reg.submit_dossier(case["case_id"], icd10_codes=["S01.0"],
                               acts=[{"code": "CONS-ED",
                                      "charged_minor": 1}],
                               discharge_ref="", day=2)


class TestAdjudication(unittest.TestCase):
    def test_tariff_prices_and_glosa_carries_reason(self):
        reg = _reg()
        cid = _to_dossier(reg)
        adj = reg.adjudicate(cid, day=12)
        self.assertEqual(adj["verdict"], "partial")
        self.assertEqual(adj["approved_minor"], 7000 + 12000)
        self.assertEqual(adj["glosa"][0]["reason"], "above-tariff")
        self.assertEqual(adj["glosa"][0]["deducted_minor"], 3000)

    def test_unknown_code_routes_to_human_never_a_guess(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.cases[cid]["dossier"]["acts"].append(
            {"code": "EXOTIC-1", "charged_minor": 999})
        adj = reg.adjudicate(cid, day=12)
        self.assertEqual(adj["verdict"], "manual-review")
        self.assertIn("EXOTIC-1", adj["manual_review_codes"])

    def test_unsigned_override_refuses(self):
        reg = _reg()
        cid = _to_dossier(reg)
        with self.assertRaises(ClaimsError):
            reg.adjudicate(cid, day=12,
                           override={"by": "", "justification": ""})

    def test_effective_dated_tariffs(self):
        price, ver = tariff_for("cv-tariffs", "CONS-GP", day=10)
        self.assertEqual((price, ver), (3500, "cv-tariffs@1.0.0"))
        price, ver = tariff_for("cv-tariffs", "CONS-GP", day=400)
        self.assertEqual((price, ver), (3800, "cv-tariffs@2.0.0"))

    def test_adjudication_sla_breach_recorded(self):
        reg = _reg()
        cid = _to_dossier(reg)
        adj = reg.adjudicate(cid, day=30)  # dossier day 11 + 19d
        self.assertTrue(any(b["clock"] == "adjudication"
                            for b in reg.cases[cid]["breaches"]))
        self.assertEqual(adj["elapsed_days"], 19)


class TestPaymentRun(unittest.TestCase):
    def test_paid_with_remittance_advice(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.adjudicate(cid, day=12)
        paid = reg.payment_run(day=13)
        self.assertEqual(len(paid), 1)
        self.assertEqual(paid[0]["net_minor"], 19_000)
        self.assertEqual(paid[0]["glosa_total_minor"], 3000)
        self.assertEqual(reg.cases[cid]["state"], "paid")

    def test_manual_review_is_not_paid(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.cases[cid]["dossier"]["acts"].append(
            {"code": "EXOTIC-1", "charged_minor": 1})
        reg.adjudicate(cid, day=12)
        self.assertEqual(reg.payment_run(day=13), [])

    def test_payment_sla_breach_recorded(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.adjudicate(cid, day=12)
        reg.payment_run(day=50)  # 38 days past adjudication
        self.assertTrue(any(b["clock"] == "payment"
                            for b in reg.cases[cid]["breaches"]))


class TestAppeals(unittest.TestCase):
    def test_appeal_resolves_within_window(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.adjudicate(cid, day=12)
        reg.appeal(cid, by="provider", grounds="glosa disputed", day=15)
        res = reg.resolve_appeal(cid, upheld=False, by="medical-director",
                                 note="tariff table governs", day=18)
        self.assertFalse(res["upheld"])
        self.assertEqual(reg.cases[cid]["state"], "closed")

    def test_appeal_after_window_refuses(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.adjudicate(cid, day=12)
        with self.assertRaises(ClaimsError):
            reg.appeal(cid, by="provider", grounds="late", day=50)

    def test_late_resolution_is_a_named_breach(self):
        reg = _reg()
        cid = _to_dossier(reg)
        reg.adjudicate(cid, day=12)
        reg.appeal(cid, by="member", grounds="denial", day=13)
        reg.resolve_appeal(cid, upheld=True, by="md", note="ok", day=25)
        self.assertTrue(any(b["clock"] == "appeal"
                            for b in reg.cases[cid]["breaches"]))


class TestFraudAndReport(unittest.TestCase):
    def test_fraud_sample_is_deterministic(self):
        reg = _reg()
        for i in range(20):
            cid = _to_dossier(reg, intake_h=100 + i * 10,
                              gop_h=101 + i * 10)
            reg.adjudicate(cid, day=12)
        s1 = reg.fraud_sample(seed="q1", day=13)
        s2 = reg.fraud_sample(seed="q1", day=13)
        self.assertEqual(s1, s2)
        self.assertEqual(s1["population"], 20)
        s3 = reg.fraud_sample(seed="q2", day=13)
        self.assertNotEqual(s1["sampled"], s3["sampled"])

    def test_report_shows_denominators(self):
        reg = _reg()
        cid = _to_dossier(reg, gop_h=110)  # GOP breach
        reg.adjudicate(cid, day=12)
        reg.payment_run(day=13)
        rep = reg.report()
        self.assertEqual(rep["cases"], 1)
        self.assertEqual(rep["gop_sla"], {"compliant": 0, "total": 1})
        self.assertEqual(rep["paid_minor"], 19_000)
        self.assertEqual(rep["glosa_total_minor"], 3000)


if __name__ == "__main__":
    unittest.main()
