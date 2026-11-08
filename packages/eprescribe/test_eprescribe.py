"""Tests for eprescribe — the national prescription rail."""

import pytest

from eprescribe import (Error, EPrescription, PrescriptionRail, VERSION,
                        MAX_VALID_DAYS)


@pytest.fixture
def rail():
    r = PrescriptionRail()
    r.register_pharmacy("PH-1", "Farmácia Central", day=1)
    r.register_payer("NHIA", "National Health Insurance Authority")
    r.issue("RX-1", "p-ada", "dr-okafor", "NG-LUTH", 1,
            [{"drug_code": "NG-AMOX500", "dose_text": "500mg tds 7d",
              "quantity": 21},
             {"drug_code": "NG-PARA500", "dose_text": "1g prn",
              "quantity": 20}],
            payer="NHIA")
    return r


class TestIssue:
    def test_issue_assigns_token(self, rail):
        rx = rail.prescriptions["RX-1"]
        assert rx.token.startswith("RX-") and len(rx.token) == 15
        assert rx.state == "issued"

    def test_issue_requires_prescriber(self, rail):
        with pytest.raises(Error, match="prescriber"):
            rail.issue("RX-2", "p-x", "", "NG-LUTH", 1,
                       [{"drug_code": "NG-AMOX500", "quantity": 21}])

    def test_issue_rejects_empty_items(self, rail):
        with pytest.raises(Error, match="at least one item"):
            rail.issue("RX-2", "p-x", "dr-a", "NG-LUTH", 1, [])

    def test_issue_bounds_validity(self, rail):
        with pytest.raises(Error, match="bounded"):
            rail.issue("RX-2", "p-x", "dr-a", "NG-LUTH", 1,
                       [{"drug_code": "NG-AMOX500", "quantity": 10}],
                       valid_days=MAX_VALID_DAYS + 1)

    def test_unknown_payer_refuses(self, rail):
        with pytest.raises(Error, match="payer"):
            rail.issue("RX-2", "p-x", "dr-a", "NG-LUTH", 1,
                       [{"drug_code": "NG-AMOX500", "quantity": 10}],
                       payer="NONEXISTENT")

    def test_duplicate_rx_id_refuses(self, rail):
        with pytest.raises(Error, match="already exists"):
            rail.issue("RX-1", "p-x", "dr-a", "NG-LUTH", 1,
                       [{"drug_code": "NG-AMOX500", "quantity": 10}])

    def test_token_is_deterministic(self, rail):
        rx2 = EPrescription("RX-1", "p-ada", "dr-okafor", "NG-LUTH", 1,
                            [{"drug_code": "NG-AMOX500", "quantity": 21}])
        # same identity inputs, different item set -> different token
        assert rx2.token != rail.prescriptions["RX-1"].token


class TestDispense:
    def test_partial_dispense_states_remainder(self, rail):
        rx = rail.dispense("RX-1", "PH-1", 0, 10, by="pharm-1", day=2)
        assert rx.state == "partially-dispensed"
        assert rx.remaining(0) == 11

    def test_full_dispense_closes(self, rail):
        rail.dispense("RX-1", "PH-1", 0, 21, by="pharm-1", day=2)
        rx = rail.dispense("RX-1", "PH-1", 1, 20, by="pharm-1", day=2)
        assert rx.state == "dispensed"

    def test_over_dispense_refuses_with_remainder(self, rail):
        with pytest.raises(Error, match="21 unit"):
            rail.dispense("RX-1", "PH-1", 0, 22, by="pharm-1", day=2)

    def test_unregistered_pharmacy_refuses(self, rail):
        with pytest.raises(Error, match="not on the rail"):
            rail.dispense("RX-1", "PH-99", 0, 1, by="pharm-1", day=2)

    def test_deactivated_pharmacy_refuses(self, rail):
        rail.deactivate_pharmacy("PH-1", day=2, reason="licence lapsed")
        with pytest.raises(Error, match="not on the rail"):
            rail.dispense("RX-1", "PH-1", 0, 1, by="pharm-1", day=3)

    def test_dispense_after_terminal_refuses(self, rail):
        rail.dispense("RX-1", "PH-1", 0, 21, by="pharm-1", day=2)
        rail.dispense("RX-1", "PH-1", 1, 20, by="pharm-1", day=2)
        with pytest.raises(Error, match="fully dispensed"):
            rail.dispense("RX-1", "PH-1", 0, 1, by="pharm-1", day=3)

    def test_token_resolution(self, rail):
        rx = rail.resolve_token(rail.prescriptions["RX-1"].token)
        assert rx.rx_id == "RX-1"
        with pytest.raises(Error, match="unknown token"):
            rail.resolve_token("RX-000000000000")


class TestCancelExpireRenew:
    def test_cancel_is_final_and_reasoned(self, rail):
        rail.cancel("RX-1", by="dr-okafor", day=3, reason="ADR reported")
        with pytest.raises(Error, match="cancelled"):
            rail.dispense("RX-1", "PH-1", 0, 1, by="pharm-1", day=4)
        with pytest.raises(Error, match="final"):
            rail.cancel("RX-1", by="dr-okafor", day=4, reason="again")

    def test_only_prescriber_cancels(self, rail):
        with pytest.raises(Error, match="prescriber"):
            rail.cancel("RX-1", by="dr-other", day=3, reason="x")

    def test_expire_sweep(self, rail):
        assert rail.expire_sweep(32) == ["RX-1"]
        with pytest.raises(Error, match="expired"):
            rail.dispense("RX-1", "PH-1", 0, 1, by="pharm-1", day=33)

    def test_renewal_cycle(self, rail):
        rail.cancel("RX-1", by="dr-okafor", day=3, reason="course done")
        rail.request_renewal("RX-1", by="PH-1", day=4,
                             note="patient still symptomatic")
        assert rail.decide_renewal("RX-1", by="dr-okafor", day=5,
                                   approved=False,
                                   reason="needs review first") is False

    def test_renewal_refusal_needs_reason(self, rail):
        rail.cancel("RX-1", by="dr-okafor", day=3, reason="course done")
        rail.request_renewal("RX-1", by="PH-1", day=4, note="please")
        with pytest.raises(Error, match="states why"):
            rail.decide_renewal("RX-1", by="dr-okafor", day=5,
                                approved=False)


class TestFeedback:
    def test_dispensation_history_is_feedback(self, rail):
        rail.dispense("RX-1", "PH-1", 0, 10, by="pharm-1", day=2)
        hist = rail.dispensation_history("RX-1")
        assert len(hist) == 1 and hist[0]["quantity"] == 10
        assert hist[0]["pharmacy"] == "PH-1"

    def test_never_collected(self, rail):
        rail.issue("RX-2", "p-bola", "dr-okafor", "NG-LUTH", 1,
                   [{"drug_code": "NG-AMOX500", "quantity": 21}])
        rail.dispense("RX-1", "PH-1", 0, 5, by="pharm-1", day=2)
        nc = rail.never_collected(10)
        assert [r["rx_id"] for r in nc] == ["RX-2"]
        assert nc[0]["days_open"] == 9

    def test_audit_carries_checksum_and_version(self, rail):
        a = rail.audit("RX-1")
        assert a["sha256"] == rail.prescriptions["RX-1"].fingerprint()
        assert a["version"] == VERSION
