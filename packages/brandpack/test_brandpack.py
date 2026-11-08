"""Tests for brandpack — brand entries as doctrine, CSS as artifact."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from brandpack import (VERSION, BrandRegistry, Error,  # noqa: E402
                       REFERENCE_BRANDS, emit_css, export_css,
                       seed_reference)


def _reg():
    return BrandRegistry()


def _brand(**over):
    d = {"wordmark": "OpenHealth · Ghana", "operator": "NHIS-GH",
         "logo_ref": "icons/logo-gh.svg", "hero_ref": "icons/hero-gh.svg",
         "direction": "ltr", "palette": {"header": "#123456"}}
    d.update(over)
    return d


class TestEntries:
    def test_complete_entry_proposes(self):
        reg = _reg()
        reg.propose("gh", _brand(), by="design-lead", day=1)
        assert reg.brands["gh"]["status"] == "proposed"

    def test_incomplete_entry_refuses(self):
        reg = _reg()
        with pytest.raises(Error, match="missing"):
            reg.propose("gh", {"wordmark": "x"}, by="a", day=1)

    def test_unknown_theme_token_refuses(self):
        reg = _reg()
        with pytest.raises(Error, match="unknown theme token"):
            reg.propose("gh", _brand(palette={"sparkle": "#ffffff"}),
                        by="a", day=1)

    def test_non_hex_colour_refuses(self):
        reg = _reg()
        with pytest.raises(Error, match="hex"):
            reg.propose("gh", _brand(palette={"header": "blueish"}),
                        by="a", day=1)

    def test_direction_is_stated(self):
        reg = _reg()
        with pytest.raises(Error, match="direction"):
            reg.propose("gh", _brand(direction="sideways"),
                        by="a", day=1)


class TestFourEyes:
    def test_ratify_needs_a_second_hand(self):
        reg = _reg()
        reg.propose("gh", _brand(), by="design-lead", day=1)
        with pytest.raises(Error, match="never approves"):
            reg.ratify("gh", by="design-lead", day=1)
        reg.ratify("gh", by="programme-director", day=1)
        assert reg.live("gh")["status"] == "live"

    def test_retire_states_reason(self):
        reg = _reg()
        reg.propose("gh", _brand(), by="a", day=1)
        reg.ratify("gh", by="b", day=1)
        with pytest.raises(Error, match="reason"):
            reg.retire("gh", by="a", day=2, reason="")
        reg.retire("gh", by="a", day=2, reason="rebrand")
        with pytest.raises(Error, match="no live brand"):
            reg.live("gh")

    def test_live_brand_refuses_reproposal(self):
        reg = _reg()
        reg.propose("gh", _brand(), by="a", day=1)
        reg.ratify("gh", by="b", day=1)
        with pytest.raises(Error, match="retire before"):
            reg.propose("gh", _brand(), by="a", day=2)


class TestEmit:
    def test_css_is_deterministic(self):
        reg = seed_reference()
        a = emit_css(reg.live("ng"))
        b = emit_css(reg.live("ng"))
        assert a == b
        assert "--oh-header" in a
        assert "GENERATED from brandpack" in a

    def test_reference_brands_are_live(self):
        reg = seed_reference()
        assert set(reg.live()) == {"ng", "cv"}
        assert reg.live("cv")["operator"] == "INPS-CV"

    def test_export_writes_artifacts(self, tmp_path):
        paths = export_css(str(tmp_path))
        assert len(paths) == len(REFERENCE_BRANDS)
        for p in paths:
            assert os.path.exists(p)

    def test_audit_is_checksummed(self):
        rep = seed_reference().audit()
        assert rep["live"] == 2
        assert len(rep["sha256"]) == 64

    def test_version_stamp(self):
        assert VERSION == "brandpack@1.0.0"
