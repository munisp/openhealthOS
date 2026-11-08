"""Phase B tests — FX rate feeds, fraud feedback loop, reinsurance."""

import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhfraudgraph import (FraudGraph, FraudGraphError,  # noqa: E402
                          OutcomeFeedback, ScoringRegistry)
from vhreinsure import (FactorRegistry, ReinsuranceLedger,  # noqa: E402
                        ReinsureError, TreatyRegistry,
                        VHREINSURE_VERSION)
from vhtreasury import RateFeed, TreasuryError  # noqa: E402


# ── Rate feeds ────────────────────────────────────────────────────

class TestRateFeed:
    def test_fresh_rate_converts(self):
        feed = RateFeed("ECB", max_age_days=2)
        feed.update("NGN", per_eur=1700.0, feed_day=10)
        # 1,700,000 minor (kobo) = ₦17,000 = €10 at 1700/EUR
        assert feed.eur_value_minor("NGN", 1_700_000, today=11) \
            == 1000

    def test_stale_rate_refuses(self):
        feed = RateFeed("ECB", max_age_days=2)
        feed.update("NGN", per_eur=1700.0, feed_day=1)
        with pytest.raises(TreasuryError, match="do not convert"):
            feed.eur_value_minor("NGN", 1000, today=10)

    def test_peg_is_declared_fallback(self):
        feed = RateFeed("ECB", max_age_days=2)
        # CVE not in feed; statutory peg in the static registry.
        # 1,102,650 centavos = CVE 11,026.50 / 110.265 = EUR 100.00
        assert feed.eur_value_minor("CVE", 1_102_650, today=99) \
            == 10000

    def test_no_rate_no_peg_states_unpriced(self):
        feed = RateFeed("ECB", max_age_days=2)
        with pytest.raises(TreasuryError, match="never zeroed"):
            feed.eur_value_minor("GHS", 1000, today=1)

    def test_feeds_are_named_and_bounded(self):
        with pytest.raises(TreasuryError, match="names its source"):
            RateFeed("", max_age_days=2)
        with pytest.raises(TreasuryError,
                           match="declared positive"):
            RateFeed("ECB", max_age_days=0)

    def test_missing_feed_rate_is_stated(self):
        feed = RateFeed("ECB", max_age_days=2)
        with pytest.raises(TreasuryError, match="never zeroed"):
            feed.age_days("ZAR", today=1)


# ── Fraud feedback loop ───────────────────────────────────────────

def _ring():
    g = FraudGraph()
    for i in range(3):
        g.add_claim(claim_ref=f"C{i}", claimant=f"cl-{i}",
                    provider="clinic-x", payout_account="acct-9")
    return g.detect_rings()[0]


class TestOutcomeFeedback:
    def test_confirmed_ring_raises_scores(self):
        fb = OutcomeFeedback()
        fb.record_outcome(_ring(), outcome="confirmed-fraud",
                          by="committee", day=20)
        s = fb.risk_score("clinic-x")
        assert s["score"] == 40 and s["band"] == "watch"
        assert s["explanation"][0]["outcome"] == "confirmed-fraud"

    def test_cleared_ring_lowers_score(self):
        fb = OutcomeFeedback()
        fb.record_outcome(_ring(), outcome="cleared", by="court",
                          day=20)
        s = fb.risk_score("acct-9", open_detections=2)
        assert s["score"] == -15 + 20
        assert any("open_detections" in e for e in s["explanation"])

    def test_double_adjudication_refuses(self):
        fb = OutcomeFeedback()
        ring = _ring()
        fb.record_outcome(ring, outcome="cleared", by="court", day=1)
        with pytest.raises(FraudGraphError, match="not an overwrite"):
            fb.record_outcome(ring, outcome="confirmed-fraud",
                              by="court", day=2)

    def test_unknown_outcome_refuses(self):
        with pytest.raises(FraudGraphError, match="unknown outcome"):
            OutcomeFeedback().record_outcome(
                _ring(), outcome="probably-fine", by="x", day=1)

    def test_weights_are_four_eyes(self):
        reg = ScoringRegistry()
        reg.propose({"confirmed_ring": 60}, by="analyst", day=1)
        with pytest.raises(FraudGraphError, match="never ratifies"):
            reg.ratify(by="analyst", day=1)
        w = reg.ratify(by="fraud-head", day=1)
        assert w["confirmed_ring"] == 60

    def test_unknown_weight_refuses(self):
        with pytest.raises(FraudGraphError, match="unknown scoring"):
            ScoringRegistry().propose({"gut_feel": 99}, by="a", day=1)

    def test_uninvolved_subject_scores_zero_explained(self):
        fb = OutcomeFeedback()
        fb.record_outcome(_ring(), outcome="confirmed-fraud",
                          by="committee", day=1)
        s = fb.risk_score("bystander-clinic")
        assert s["score"] == 0 and s["explanation"] == []


# ── Reinsurance & solvency ────────────────────────────────────────

def _ledger():
    led = ReinsuranceLedger()
    led.treaties.propose("QS-2026", {"kind": "quota-share",
                                     "reinsurer": "Africa-Re",
                                     "cession_pct": 30},
                         by="cfo", day=1)
    led.treaties.ratify("QS-2026", by="ceo", day=1)
    led.treaties.propose("XL-CAT", {"kind": "excess-of-loss",
                                    "reinsurer": "Swiss-Re",
                                    "attachment_eur_minor": 50_000_00,
                                    "limit_eur_minor": 500_000_00},
                         by="cfo", day=1)
    led.treaties.ratify("XL-CAT", by="ceo", day=1)
    return led


class TestTreaties:
    def test_four_eyes(self):
        reg = TreatyRegistry()
        reg.propose("T1", {"kind": "quota-share",
                           "reinsurer": "Africa-Re",
                           "cession_pct": 25}, by="cfo", day=1)
        with pytest.raises(ReinsureError, match="never ratifies"):
            reg.ratify("T1", by="cfo", day=1)

    def test_unratified_cession_refuses(self):
        led = ReinsuranceLedger()
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=100_00, day=5)
        with pytest.raises(ReinsureError, match="not ratified"):
            led.cede("GHOST-TREATY")

    def test_xol_requires_attachment_and_limit(self):
        reg = TreatyRegistry()
        with pytest.raises(ReinsureError,
                           match="attachment and limit"):
            reg.propose("XL", {"kind": "excess-of-loss",
                               "reinsurer": "R"}, by="cfo", day=1)


class TestCession:
    def test_quota_share_arithmetic(self):
        led = _ledger()
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=10_000_00, day=5)
        rec = led.cede("QS-2026")[0]
        assert rec["recovered_eur_minor"] == 3_000_00
        assert rec["terms"]["cession_pct"] == 30

    def test_xol_layer_arithmetic(self):
        led = _ledger()
        led.register_claim("CL-CAT", line="medical",
                           gross_eur_minor=600_000_00, day=5)
        rec = led.cede("XL-CAT")[0]
        # 600k gross - 50k attachment = 550k, capped at 500k limit
        assert rec["recovered_eur_minor"] == 500_000_00

    def test_ceding_nothing_is_stated(self):
        led = _ledger()
        with pytest.raises(ReinsureError, match="stated, not zeroed"):
            led.cede("QS-2026", line="empty-line")


class TestReservingAndSolvency:
    def test_ibnr_requires_ratified_factors(self):
        led = _ledger()
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=100_00, day=5)
        with pytest.raises(ReinsureError, match="never guesses"):
            led.ibnr_reserve("medical", as_of_day=30)

    def test_ibnr_arithmetic(self):
        led = _ledger()
        led.factors.propose("medical", 1.25,
                            basis="2023-2025 triangles", by="actuary",
                            day=1)
        led.factors.ratify("medical", by="chief-actuary", day=1)
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=10_000_00,
                           paid_eur_minor=4_000_00, day=5)
        r = led.ibnr_reserve("medical", as_of_day=30)
        assert r["outstanding_eur_minor"] == 6_000_00
        assert r["ibnr_eur_minor"] == 1_500_00   # 6000 x 0.25

    def test_factor_below_one_refuses(self):
        reg = FactorRegistry()
        with pytest.raises(ReinsureError, match="miracle"):
            reg.propose("medical", 0.9, basis="wishful", by="a", day=1)

    def test_solvency_breach_is_stated(self):
        led = _ledger()
        led.factors.propose("medical", 1.2, basis="tri", by="a", day=1)
        led.factors.ratify("medical", by="b", day=1)
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=10_000_00, day=5)
        rep = led.solvency_report(
            written_premium_eur_minor=100_000_00,
            available_capital_eur_minor=5_000_00,
            required_pct_of_premium=25, lines=["medical"],
            as_of_day=30)
        assert rep["solvency_ratio"] < 1
        assert "BREACH" in rep["state"]
        assert len(rep["sha256"]) == 64

    def test_solvent_report(self):
        led = _ledger()
        led.factors.propose("medical", 1.2, basis="tri", by="a", day=1)
        led.factors.ratify("medical", by="b", day=1)
        led.register_claim("CL-1", line="medical",
                           gross_eur_minor=10_000_00,
                           paid_eur_minor=10_000_00, day=5)
        rep = led.solvency_report(
            written_premium_eur_minor=100_000_00,
            available_capital_eur_minor=50_000_00,
            required_pct_of_premium=25, lines=["medical"],
            as_of_day=30)
        assert rep["state"] == "solvent" and rep["solvency_ratio"] == 2.0

    def test_version(self):
        assert VHREINSURE_VERSION == "vhreinsure@1.0.0"
