"""Phase D tests — partner-scale riders, continuity wallet, data
cooperative, airline-disruption API."""

import hashlib
import hmac
import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from vhcoop import CoopDesk, CoopError, CoopRegistry, VHCOOP_VERSION  # noqa: E402
from vhdisrupt import DisruptionAPI, DisruptError, VHDISRUPT_VERSION  # noqa: E402
from vhextract import ExtractDesk  # noqa: E402
from vhrider import (ManifestRider, PartnerRiderRegistry,  # noqa: E402
                     RiderEngine, RiderError)
from vhwallet import (ContinuityWallet, VHWALLET_VERSION,  # noqa: E402
                      WalletError)
from vhroam import compose_roam_profile  # noqa: E402


class _Ring:
    def __init__(self):
        self.keys = {"k1": {"secret": "oracle-secret"}}


def _attest(engine, flight="KQ784", delay=300, day=10):
    payload = f"{flight}|delay|{delay}|{day}".encode()
    sig = hmac.new(b"oracle-secret", payload,
                   hashlib.sha256).hexdigest().encode()
    import vhrider
    vhrider.register_oracle("o1", signature_key_id="k1",
                            source="fed-notam")
    return engine.attest("o1", flight=flight, disruption="delay",
                         delay_minutes=delay, signature=sig, day=day)


# ── Partner-scale parametric rider ────────────────────────────────

class TestPartnerRider:
    def _partners(self):
        reg = PartnerRiderRegistry()
        reg.propose("cruise-cv", {"kind": "cruise",
                                  "payout_table": [
                                      {"min_delay_minutes": 360,
                                       "factor": 1.0},
                                      {"min_delay_minutes": 180,
                                       "factor": 0.5}]},
                    by="product", day=1)
        reg.ratify("cruise-cv", by="coo", day=1)
        return reg

    def test_partner_table_is_own_and_ratified(self):
        reg = PartnerRiderRegistry()
        with pytest.raises(RiderError, match="never smuggled"):
            reg.propose("p", {"kind": "hotel"}, by="a", day=1)

    def test_unratified_partner_pays_nobody(self):
        engine = RiderEngine(key_ring=_Ring())
        ev = _attest(engine)
        reg = PartnerRiderRegistry()
        with pytest.raises(RiderError, match="not ratified"):
            ManifestRider(engine, reg).mass_trigger(
                "ghost", [{"booking_ref": "b1", "unit_price_minor": 1,
                           "cover_ref": "c1"}],
                event_sha256=ev["event_sha256"], day=10)

    def test_mass_trigger_pays_manifest(self):
        engine = RiderEngine(key_ring=_Ring())
        ev = _attest(engine, delay=300)
        reg = self._partners()
        manifest = [{"booking_ref": f"b{i}",
                     "unit_price_minor": 20_000_00,
                     "cover_ref": f"c{i}"} for i in range(3)]
        stmt = ManifestRider(engine, reg).mass_trigger(
            "cruise-cv", manifest,
            event_sha256=ev["event_sha256"], day=10)
        # 300 min >= 180 -> 0.5 x 20,000 EUR = 10,000 each
        assert stmt["bookings"] == 3 and stmt["paid"] == 30_000_00
        assert len(stmt["sha256"]) == 64
        assert all(l["evidence_sha256"] == ev["event_sha256"]
                   for l in stmt["lines"])

    def test_no_attested_event_no_payout(self):
        engine = RiderEngine(key_ring=_Ring())
        reg = self._partners()
        with pytest.raises(RiderError, match="attested, not "
                           "asserted"):
            ManifestRider(engine, reg).mass_trigger(
                "cruise-cv", [{"booking_ref": "b",
                               "unit_price_minor": 1,
                               "cover_ref": "c"}],
                event_sha256="0" * 64, day=10)

    def test_empty_manifest_stated(self):
        engine = RiderEngine(key_ring=_Ring())
        ev = _attest(engine)
        reg = self._partners()
        with pytest.raises(RiderError, match="pays nobody"):
            ManifestRider(engine, reg).mass_trigger(
                "cruise-cv", [], event_sha256=ev["event_sha256"],
                day=10)


# ── Continuity wallet ─────────────────────────────────────────────

class TestWallet:
    def _wallet(self):
        return ContinuityWallet(key_ring=_Ring())

    def _roam(self):
        return compose_roam_profile("ecowas", countries=("ng", "gh"),
                                    label="ECOWAS")

    def test_issue_and_verify_offline(self):
        w = self._wallet()
        token = w.issue(holder_ref="trav-1", roam_profile=self._roam(),
                        member_verdicts=["green", "amber"],
                        ips_fingerprint="a" * 64,
                        valid_from_day=10, valid_to_day=40,
                        key_id="k1", by="issuer", day=10)
        out = w.verify_offline(token, today=20)
        assert out["state"] == "ok"
        assert out["verdict"] == "amber"     # strictest wins
        assert out["ips_fingerprint"] == "a" * 64

    def test_expired_refuses(self):
        w = self._wallet()
        token = w.issue(holder_ref="t", roam_profile=self._roam(),
                        member_verdicts=["green", "green"],
                        ips_fingerprint="b" * 64,
                        valid_from_day=1, valid_to_day=10,
                        key_id="k1", by="i", day=1)
        out = w.verify_offline(token, today=11)
        assert out["state"] == "expired"

    def test_revoked_never_stale_green(self):
        w = self._wallet()
        token = w.issue(holder_ref="t", roam_profile=self._roam(),
                        member_verdicts=["green", "green"],
                        ips_fingerprint="b" * 64,
                        valid_from_day=1, valid_to_day=99,
                        key_id="k1", by="i", day=1)
        w.revoke_cover(holder_ref="t", reason="premium default",
                       by="ops", day=5)
        out = w.verify_offline(token, today=6)
        assert out["state"] == "revoked"

    def test_tampered_checksum_refuses(self):
        w = self._wallet()
        token = w.issue(holder_ref="t", roam_profile=self._roam(),
                        member_verdicts=["green", "green"],
                        ips_fingerprint="b" * 64,
                        valid_from_day=1, valid_to_day=99,
                        key_id="k1", by="i", day=1)
        token["verdict"] = "green"   # tamper: soften the verdict
        token["sha256"] = "f" * 64
        out = w.verify_offline(token, today=5)
        assert out["state"] == "tampered"

    def test_record_stays_with_traveler(self):
        w = self._wallet()
        with pytest.raises(WalletError, match="not the record"):
            w.issue(holder_ref="t", roam_profile=self._roam(),
                    member_verdicts=["green"], ips_fingerprint="short",
                    valid_from_day=1, valid_to_day=9, key_id="k1",
                    by="i", day=1)


# ── Data cooperative ──────────────────────────────────────────────

def _coop_with_sales():
    ex = ExtractDesk()
    ex.buy("corridor-loss-ratios@1.0.0", buyer="reinsurer-a", day=5,
           payment_ref="pay-1")
    reg = CoopRegistry()
    reg.propose_table({"op-nigeria": 40, "op-caboverde": 30,
                       "scheme-fund": 30}, by="coop-sec", day=1)
    reg.ratify_table(by="board-chair", day=1)
    return CoopDesk(reg, ex)


class TestCoop:
    def test_table_must_sum_100(self):
        reg = CoopRegistry()
        with pytest.raises(CoopError, match="the contract"):
            reg.propose_table({"a": 50, "b": 40}, by="x", day=1)

    def test_no_table_no_distribution(self):
        ex = ExtractDesk()
        with pytest.raises(CoopError, match="distributes nothing"):
            CoopDesk(CoopRegistry(), ex).distribute(from_day=1,
                                                    to_day=9, day=10)

    def test_distribution_splits_by_table(self):
        # catalog price for loss-ratios-q1 drives the split
        coop = _coop_with_sales()
        stmt = coop.distribute(from_day=1, to_day=10, day=10)
        gross = stmt["lines"][0]["gross_minor"]
        cuts = {l["member"]: l["cut_minor"] for l in stmt["lines"]}
        assert cuts["op-nigeria"] == int(round(gross * 0.40))
        assert cuts["scheme-fund"] == int(round(gross * 0.30))
        assert len(stmt["sha256"]) == 64

    def test_member_statement_auditable(self):
        coop = _coop_with_sales()
        coop.distribute(from_day=1, to_day=10, day=10)
        ms = coop.member_statement("op-caboverde")
        assert ms["share_pct"] == 30
        assert sum(ms["cuts_minor_by_currency"].values()) > 0
        with pytest.raises(CoopError, match="not a cooperative"):
            coop.member_statement("freeloader")

    def test_empty_window_stated(self):
        coop = _coop_with_sales()
        with pytest.raises(CoopError, match="stated, not zeroed"):
            coop.distribute(from_day=50, to_day=60, day=61)


# ── Airline-disruption API ────────────────────────────────────────

class TestDisruptionAPI:
    def _api(self):
        engine = RiderEngine(key_ring=_Ring())
        return DisruptionAPI(engine, burst=2), engine

    def test_origin_binding(self):
        api, _ = self._api()
        tok = api.issue_token(airline="AirPeace",
                              origin="https://flyairpeace.com",
                              by="partnerships", day=1)
        with pytest.raises(DisruptError, match="origin binding"):
            api.passenger_verdict(tok, origin="https://evil.example",
                                  flight="P47100", cover_ref="c1",
                                  day=1)

    def test_https_only_origins(self):
        api, _ = self._api()
        with pytest.raises(DisruptError, match="https"):
            api.issue_token(airline="X", origin="http://insecure",
                            by="a", day=1)

    def test_unattested_delay_is_stated(self):
        api, _ = self._api()
        tok = api.issue_token(airline="A",
                              origin="https://a.example", by="b",
                              day=1)
        out = api.passenger_verdict(tok, origin="https://a.example",
                                    flight="ZZ1", cover_ref="c1",
                                    day=1)
        assert out["state"] == "no-attested-event"
        assert len(out["sha256"]) == 64

    def test_rate_limit_stated_backpressure(self):
        api, _ = self._api()
        tok = api.issue_token(airline="A",
                              origin="https://a.example", by="b",
                              day=1)
        for _ in range(2):
            api.passenger_verdict(tok, origin="https://a.example",
                                  flight="F", cover_ref="c", day=1)
        with pytest.raises(DisruptError, match="stated backpressure"):
            api.passenger_verdict(tok, origin="https://a.example",
                                  flight="F", cover_ref="c", day=1)
        api.refill_window()
        api.passenger_verdict(tok, origin="https://a.example",
                              flight="F", cover_ref="c", day=1)


class TestVersions:
    def test_stamps(self):
        assert VHCOOP_VERSION == "vhcoop@1.0.0"
        assert VHDISRUPT_VERSION == "vhdisrupt@1.0.0"
        assert VHWALLET_VERSION == "vhwallet@1.0.0"
        import vhrider
        assert vhrider.RIDER_VERSION == "vhrider@1.1.0"
