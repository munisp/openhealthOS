import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from ipc import Error, IpcRegister, IPC_VERSION  # noqa: E402


class TestDenominators:
    def test_rate_without_denominator_refuses(self):
        reg = IpcRegister()
        reg.report_hai("p1", "clabsi", ward="icu", onset_day=5,
                       by="ipc-nurse", day=5)
        with pytest.raises(Error, match="denominator .* not stated"):
            reg.hai_rate("icu", "clabsi", from_day=1, to_day=10)

    def test_clabsi_per_1000_device_days(self):
        reg = IpcRegister()
        for d in range(1, 11):
            reg.record_device_days("icu", "central-line", 20, day=d,
                                   by="charge-nurse")
        reg.report_hai("p1", "clabsi", ward="icu", onset_day=4,
                       by="ipc", day=4)
        r = reg.hai_rate("icu", "clabsi", from_day=1, to_day=10)
        assert r["denominator"] == 200 and r["rate"] == 5.0
        assert r["per"] == 1000

    def test_ssi_per_100_procedures(self):
        reg = IpcRegister()
        reg.record_procedures("theatre-1", 50, day=1, by="cns")
        reg.report_hai("p2", "ssi", ward="theatre-1", onset_day=3,
                       by="ipc", day=3)
        r = reg.hai_rate("theatre-1", "ssi", from_day=1, to_day=7)
        assert r["per"] == 100 and r["rate"] == 2.0

    def test_wrong_device_class_refuses(self):
        with pytest.raises(Error, match="unknown device class"):
            IpcRegister().record_device_days("icu", "foley-ish", 5,
                                             day=1, by="n")


class TestIsolation:
    def test_flag_and_ward_board(self):
        reg = IpcRegister()
        reg.flag_isolation("p1", organism="MRSA",
                           precautions="contact", ward="ward-3",
                           by="ipc", day=1)
        board = reg.ward_board("ward-3")
        assert len(board) == 1 and board[0]["organism"] == "MRSA"

    def test_double_flag_refuses(self):
        reg = IpcRegister()
        reg.flag_isolation("p1", organism="MRSA", precautions="contact",
                           ward="w", by="a", day=1)
        with pytest.raises(Error, match="never double-flag"):
            reg.flag_isolation("p1", organism="VRE",
                               precautions="contact", ward="w", by="a",
                               day=2)

    def test_discontinue_needs_reason(self):
        reg = IpcRegister()
        reg.flag_isolation("p1", organism="MRSA", precautions="contact",
                           ward="w", by="a", day=1)
        with pytest.raises(Error, match="stated reason"):
            reg.discontinue_isolation("p1", reason="", by="b", day=3)
        reg.discontinue_isolation("p1", reason="two negative screens",
                                  by="ipc", day=3)
        assert reg.ward_board("w") == []

    def test_unknown_precautions_refuse(self):
        with pytest.raises(Error, match="unknown precautions"):
            IpcRegister().flag_isolation("p1", organism="x",
                                         precautions="hazmat",
                                         ward="w", by="a", day=1)


class TestHandHygiene:
    def test_compliance_computed(self):
        a = IpcRegister().hand_hygiene_audit(
            "ward-3", opportunities=40, compliant=33,
            observer="ipc-link", day=7)
        assert a["compliance"] == 0.825

    def test_zero_opportunities_refuse(self):
        with pytest.raises(Error, match="not an audit"):
            IpcRegister().hand_hygiene_audit(
                "w", opportunities=0, compliant=0, observer="o", day=1)

    def test_compliant_never_exceeds_opportunities(self):
        with pytest.raises(Error, match="not wished"):
            IpcRegister().hand_hygiene_audit(
                "w", opportunities=10, compliant=12, observer="o",
                day=1)


class TestOutbreak:
    def test_threshold_breach_is_stated(self):
        reg = IpcRegister()
        reg.declare_threshold("ward-3", "MRSA", max_active=2,
                              by="ipc-lead", day=1)
        for i in range(3):
            reg.flag_isolation(f"p{i}", organism="MRSA",
                               precautions="contact", ward="ward-3",
                               by="ipc", day=2)
        assert len(reg.outbreak_signals) == 1
        sig = reg.outbreak_signals[0]
        assert sig["active"] == 3 and "cross the declared threshold" \
            in sig["reason"]

    def test_no_threshold_no_signal(self):
        reg = IpcRegister()
        for i in range(5):
            reg.flag_isolation(f"p{i}", organism="MRSA",
                               precautions="contact", ward="w",
                               by="a", day=1)
        assert reg.outbreak_signals == []

    def test_version(self):
        assert IPC_VERSION == "ipc@1.0.0"
