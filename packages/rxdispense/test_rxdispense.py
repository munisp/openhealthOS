import os
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

from rxdispense import (DispensingDesk, Error, FormulaRegistry,  # noqa: E402
                        LotShelf, RXDISPENSE_VERSION,
                        SubstitutionRegistry)


def _desk():
    desk = DispensingDesk()
    desk.shelf.receive("NG-AMOX500", "LOT-A", quantity=100,
                       expiry_day=50, day=1)
    desk.shelf.receive("AMOX-GEN", "LOT-G1", quantity=200,
                       expiry_day=40, day=1)
    desk.register_prescription("RX-1", product="NG-AMOX500",
                               quantity=30, prescriber="dr-a", day=2)
    return desk


class TestSubstitutionRegistry:
    def test_four_eyes(self):
        reg = SubstitutionRegistry()
        reg.propose("NG-AMOX500", "AMOX-GEN", by="pharm-a", day=1)
        with pytest.raises(Error, match="never ratifies"):
            reg.ratify("NG-AMOX500", by="pharm-a", day=1)
        reg.ratify("NG-AMOX500", by="pharm-b", day=1)
        assert reg.equivalent("NG-AMOX500") == "AMOX-GEN"

    def test_unlisted_substitution_refuses(self):
        desk = _desk()
        with pytest.raises(Error, match="no ratified substitution"):
            desk.dispense("RX-1", quantity=10, product="AMOX-GEN",
                          by="pharm-a", day=3)

    def test_ratified_substitution_is_stated_on_label(self):
        desk = _desk()
        desk.substitutions.propose("NG-AMOX500", "AMOX-GEN",
                                   by="pharm-a", day=1)
        desk.substitutions.ratify("NG-AMOX500", by="pharm-b", day=1)
        fill = desk.dispense("RX-1", quantity=10, product="AMOX-GEN",
                             by="pharm-a", day=3)
        assert "substitution stated" in fill["label_note"]
        assert fill["lot_no"] == "LOT-G1"


class TestDispensing:
    def test_full_fill(self):
        desk = _desk()
        fill = desk.dispense("RX-1", quantity=30, by="pharm-a", day=3)
        assert fill["partial"] is False
        assert desk.prescriptions["RX-1"]["state"] == "filled"

    def test_partial_fill_tracks_balance(self):
        desk = _desk()
        fill = desk.dispense("RX-1", quantity=10, by="pharm-a", day=3)
        assert fill["partial"] is True and fill["balance_owed"] == 20
        owed = desk.owed_balances()
        assert owed == [{"rx_ref": "RX-1", "owed": 20}]

    def test_overfill_and_zerofill_refuse(self):
        desk = _desk()
        with pytest.raises(Error, match="overfill refuses"):
            desk.dispense("RX-1", quantity=31, by="pharm-a", day=3)
        with pytest.raises(Error, match="zero-fill refuses"):
            desk.dispense("RX-1", quantity=0, by="pharm-a", day=3)

    def test_high_alert_needs_second_pharmacist(self):
        desk = _desk()
        desk.register_prescription("RX-HA", product="NG-AMOX500",
                                   quantity=5, high_alert=True,
                                   prescriber="dr-a", day=2)
        with pytest.raises(Error, match="two pairs of eyes"):
            desk.dispense("RX-HA", quantity=5, by="pharm-a", day=3)
        with pytest.raises(Error, match="two pairs of eyes"):
            desk.dispense("RX-HA", quantity=5, by="pharm-a",
                          verified_by="pharm-a", day=3)
        fill = desk.dispense("RX-HA", quantity=5, by="pharm-a",
                             verified_by="pharm-b", day=3)
        assert fill["verified_by"] == "pharm-b"

    def test_unknown_prescription_refuses(self):
        with pytest.raises(Error, match="no open prescription"):
            _desk().dispense("RX-99", quantity=1, by="p", day=1)

    def test_double_registration_refuses(self):
        desk = _desk()
        with pytest.raises(Error, match="not an overwrite"):
            desk.register_prescription("RX-1", product="X", quantity=1,
                                       prescriber="d", day=3)


class TestLots:
    def test_fefo_picks_earliest_expiry(self):
        shelf = LotShelf()
        shelf.receive("P", "LOT-LATE", quantity=10, expiry_day=90,
                      day=1)
        shelf.receive("P", "LOT-EARLY", quantity=10, expiry_day=30,
                      day=1)
        assert shelf.pick_fefo("P", 5, today=10)["lot_no"] == \
            "LOT-EARLY"

    def test_expired_lot_refuses(self):
        shelf = LotShelf()
        shelf.receive("P", "LOT-X", quantity=10, expiry_day=5, day=1)
        with pytest.raises(Error, match="no unexpired stock"):
            shelf.pick_fefo("P", 5, today=10)
        with pytest.raises(Error, match="never dispensed"):
            shelf.draw("P", "LOT-X", 1, today=10)

    def test_short_stock_is_stated(self):
        shelf = LotShelf()
        shelf.receive("P", "LOT-X", quantity=3, expiry_day=50, day=1)
        with pytest.raises(Error, match="partial fill is the honest"):
            shelf.pick_fefo("P", 10, today=1)


class TestCompounding:
    def _formula(self, reg):
        reg.propose("ORS-1L", [{"product": "NG-AMOX500",
                                "quantity": 2}], by="pharm-a", day=1)
        reg.ratify("ORS-1L", by="pharm-b", day=1)

    def test_compound_batch_checksummed(self):
        desk = _desk()
        self._formula(desk.formulas)
        desk.register_prescription("RX-C", product="ORS-1L",
                                   quantity=1, prescriber="dr-a",
                                   day=2)
        batch = desk.compound("RX-C", formula_id="ORS-1L",
                              by="pharm-a", verified_by="pharm-b",
                              day=3)
        assert len(batch["sha256"]) == 64
        assert len(batch["formula_sha256"]) == 64

    def test_unratified_formula_refuses(self):
        desk = _desk()
        desk.formulas.propose("F1", [{"product": "NG-AMOX500",
                                      "quantity": 1}], by="a", day=1)
        desk.register_prescription("RX-C", product="F1", quantity=1,
                                   prescriber="d", day=2)
        with pytest.raises(Error, match="not ratified"):
            desk.compound("RX-C", formula_id="F1", by="pharm-a",
                          verified_by="pharm-b", day=3)

    def test_mixer_never_verifies_alone(self):
        desk = _desk()
        self._formula(desk.formulas)
        desk.register_prescription("RX-C", product="ORS-1L",
                                   quantity=1, prescriber="dr-a",
                                   day=2)
        with pytest.raises(Error, match="never checks alone"):
            desk.compound("RX-C", formula_id="ORS-1L", by="pharm-a",
                          verified_by="pharm-a", day=3)

    def test_version(self):
        assert RXDISPENSE_VERSION == "rxdispense@1.0.0"
