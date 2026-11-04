"""Tests for vhproviders — tariff administration under four-eyes,
accreditation workflow, portal scoping, and the weekly MoH pack."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from vhclaims import ClaimsRegistry  # noqa: E402
from vhproviders import (ProviderError, ProviderPortal,  # noqa: E402
                         ProviderRegistry, TariffBook)


def _accredited(providers, pid="HOSP-1"):
    providers.apply(pid, legal_name="Hospital One", licence_ref="LIC-1",
                    services=["emergency", "observation"], day=1)
    providers.verify(pid, by="inspector-1",
                     checks=["licence-valid", "facility-visited"], day=2)
    providers.accredit(pid, by="director-1", day=3)


def _portal(with_case=True):
    claims = ClaimsRegistry(rules_version="vhc-claims@1.0.0",
                            tariff_set="cv-tariffs")
    providers = ProviderRegistry()
    if with_case:
        _accredited(providers)
    portal = ProviderPortal(claims=claims, providers=providers)
    case_id = None
    if with_case:
        case = claims.intake(channel="phone", cover_ref="VHC-1",
                             claimant_name="Ada", summary="ED visit",
                             now_h=100, day=10)
        claims.issue_gop(case["case_id"], provider_ref="HOSP-1",
                         limit_minor=100_000, now_h=101)
        case_id = case["case_id"]
    return portal, claims, providers, case_id


class TestTariffBook(unittest.TestCase):
    def test_propose_approve_lookup(self):
        book = TariffBook(book_id="cv")
        book.propose(version="cv-t@1", acts={"CONS-GP": 3500},
                     effective_from=0, by="analyst-1", day=1)
        book.approve("cv-t@1", by="director-1", day=2)
        self.assertEqual(book.lookup("CONS-GP", day=5),
                         (3500, "cv-t@1"))

    def test_four_eyes_refusal(self):
        book = TariffBook(book_id="cv")
        book.propose(version="cv-t@1", acts={"X": 1},
                     effective_from=0, by="analyst-1", day=1)
        with self.assertRaises(ProviderError):
            book.approve("cv-t@1", by="analyst-1", day=2)

    def test_unapproved_versions_never_answer(self):
        book = TariffBook(book_id="cv")
        book.propose(version="cv-t@1", acts={"CONS-GP": 1},
                     effective_from=0, by="a", day=1)
        self.assertEqual(book.lookup("CONS-GP", day=5), (None, None))

    def test_effective_dating_and_immutability(self):
        book = TariffBook(book_id="cv")
        book.propose(version="v1", acts={"X": 100}, effective_from=0,
                     by="a", day=1)
        book.approve("v1", by="b", day=1)
        book.propose(version="v2", acts={"X": 120}, effective_from=100,
                     by="a", day=50)
        book.approve("v2", by="b", day=51)
        self.assertEqual(book.lookup("X", day=99)[0], 100)
        self.assertEqual(book.lookup("X", day=100)[0], 120)
        with self.assertRaises(ProviderError):
            book.propose(version="v1", acts={"X": 1},
                         effective_from=0, by="a", day=60)
        with self.assertRaises(ProviderError):
            book.approve("v1", by="c", day=60)  # already approved


class TestAccreditation(unittest.TestCase):
    def test_full_path(self):
        providers = ProviderRegistry()
        _accredited(providers)
        self.assertTrue(providers.is_active("HOSP-1"))

    def test_verification_needs_checks(self):
        providers = ProviderRegistry()
        providers.apply("H", legal_name="L", licence_ref="LIC",
                        services=["ed"], day=1)
        with self.assertRaises(ProviderError):
            providers.verify("H", by="i", checks=[], day=2)

    def test_verifier_never_accredits(self):
        providers = ProviderRegistry()
        providers.apply("H", legal_name="L", licence_ref="LIC",
                        services=["ed"], day=1)
        providers.verify("H", by="insp", checks=["licence"], day=2)
        with self.assertRaises(ProviderError):
            providers.accredit("H", by="insp", day=3)

    def test_suspend_and_revoke_need_reasons(self):
        providers = ProviderRegistry()
        _accredited(providers, pid="H")
        with self.assertRaises(ProviderError):
            providers.suspend("H", reason="", day=10)
        providers.suspend("H", reason="audit findings", day=10)
        self.assertFalse(providers.is_active("H"))
        providers.revoke("H", reason="licence withdrawn", day=20)
        self.assertEqual(providers.report()["by_state"]["revoked"], 1)


class TestPortal(unittest.TestCase):
    def test_unaccredited_does_not_submit(self):
        portal, claims, providers, cid = _portal(with_case=False)
        providers.apply("QUACK", legal_name="Q", licence_ref="L",
                        services=["ed"], day=1)
        case = claims.intake(channel="phone", cover_ref="V",
                             claimant_name="A", summary="s", now_h=0,
                             day=1)
        claims.issue_gop(case["case_id"], provider_ref="QUACK",
                         limit_minor=1, now_h=1)
        with self.assertRaises(ProviderError):
            portal.submit_dossier("QUACK", case["case_id"],
                                  icd10_codes=["S01.0"],
                                  acts=[{"code": "CONS-ED",
                                         "charged_minor": 1}],
                                  discharge_ref="D", day=2)

    def test_provider_bills_only_its_own_gop(self):
        portal, claims, providers, cid = _portal()
        _accredited(providers, pid="HOSP-2")
        with self.assertRaises(ProviderError):
            portal.submit_dossier("HOSP-2", cid,
                                  icd10_codes=["S01.0"],
                                  acts=[{"code": "CONS-ED",
                                         "charged_minor": 7000}],
                                  discharge_ref="D", day=11)
        portal.submit_dossier("HOSP-1", cid, icd10_codes=["S01.0"],
                              acts=[{"code": "CONS-ED",
                                     "charged_minor": 7000}],
                              discharge_ref="D", day=11)
        self.assertEqual(claims.cases[cid]["state"], "dossier-submitted")

    def test_scoped_visibility(self):
        portal, claims, providers, cid = _portal()
        other = claims.intake(channel="phone", cover_ref="VHC-2",
                              claimant_name="Bo", summary="s", now_h=0,
                              day=1)
        claims.issue_gop(other["case_id"], provider_ref="HOSP-9",
                         limit_minor=1, now_h=1)
        view = portal.my_cases("HOSP-1")
        self.assertEqual(len(view["mine"]), 1)
        self.assertEqual(view["others_count"], 1)

    def test_weekly_pack_has_denominators_and_tariff(self):
        portal, claims, providers, cid = _portal()
        book = TariffBook(book_id="cv")
        book.propose(version="cv-t@1", acts={"CONS-ED": 7000},
                     effective_from=0, by="analyst", day=1)
        book.approve("cv-t@1", by="director", day=2)
        pack = portal.weekly_moh_pack(day=14, tariff_book=book)
        self.assertEqual(pack["tariff_in_force"], "cv-t@1")
        self.assertEqual(pack["providers"]["by_state"]["accredited"], 1)
        self.assertEqual(pack["claims"]["cases"], 1)

    def test_claims_adjudicate_against_governed_book(self):
        book = TariffBook(book_id="cv")
        book.propose(version="cv-t@1", acts={"CONS-ED": 5000},
                     effective_from=0, by="analyst", day=1)
        book.approve("cv-t@1", by="director", day=2)
        claims = ClaimsRegistry(rules_version="vhc-claims@1.0.0",
                                tariff_book=book)
        case = claims.intake(channel="portal", cover_ref="V",
                             claimant_name="A", summary="s", now_h=0,
                             day=5)
        claims.issue_gop(case["case_id"], provider_ref="H",
                         limit_minor=99_000, now_h=1)
        claims.submit_dossier(case["case_id"], icd10_codes=["S01.0"],
                              acts=[{"code": "CONS-ED",
                                     "charged_minor": 7000}],
                              discharge_ref="D", day=6)
        adj = claims.adjudicate(case["case_id"], day=7)
        self.assertEqual(adj["approved_minor"], 5000)  # book price wins
        self.assertEqual(adj["glosa"][0]["deducted_minor"], 2000)


if __name__ == "__main__":
    unittest.main()
