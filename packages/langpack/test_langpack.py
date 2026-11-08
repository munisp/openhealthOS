"""Tests for langpack — languages as entries, fallback stated,
coverage computed."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langpack import (DEPTH_CHROME, LANGUAGES, VERSION, Error,  # noqa: E402
                      StringSurface, audit, build_depth_surface,
                      direction, get_language)


class TestRegistry:
    def test_african_breadth_present(self):
        for code in ("pcm", "ha", "yo", "ig", "ff", "sw", "am", "om",
                     "zu", "xh", "sn", "so", "rw", "wo", "bm", "ln",
                     "tw", "ti", "lg", "st", "af", "mg", "ee", "ki"):
            assert code in LANGUAGES, code

    def test_working_languages_present(self):
        for code in ("en", "pt", "fr", "ar"):
            assert code in LANGUAGES

    def test_direction_is_stated_per_entry(self):
        assert direction("ar") == "rtl"
        assert direction("ha") == "ltr"
        for code, entry in LANGUAGES.items():
            assert entry["direction"] in ("ltr", "rtl"), code

    def test_unknown_language_refuses(self):
        with pytest.raises(Error, match="unknown language"):
            get_language("xx")


class TestSurface:
    def test_translate_hits_requested_language(self):
        s = build_depth_surface()
        out = s.t("facility", "sw")
        assert out["text"] == "Kituo cha afya"
        assert out["fell_back"] is False

    def test_fallback_is_stated(self):
        s = StringSurface("toy")
        s.add("en", {"hello": "Hello"})
        s.add("yo", {"other": "Àríwísí"})  # present, lacks the key
        out = s.t("hello", "yo")
        assert out["text"] == "Hello"
        assert out["language"] == "en"
        assert out["fell_back"] is True

    def test_missing_key_returns_key_stated(self):
        s = StringSurface("toy2")
        s.add("en", {"hello": "Hello"})
        out = s.t("no_such_key", "ha")
        assert out["text"] == "no_such_key"
        assert out["fell_back"] is True

    def test_empty_string_refuses(self):
        s = StringSurface("toy3")
        with pytest.raises(Error, match="never blank"):
            s.add("en", {"hello": ""})

    def test_unknown_language_refuses_at_add(self):
        s = StringSurface("toy4")
        with pytest.raises(Error, match="unknown language"):
            s.add("xx", {"hello": "Hola"})


class TestCoverage:
    def test_coverage_is_computed_not_declared(self):
        s = StringSurface("toy5")
        s.add("en", {"a": "A", "b": "B", "c": "C"})
        s.add("sw", {"a": "A"})
        rep = s.coverage()
        assert rep["sw"] == {"keys": 1, "of": 3,
                             "missing": ["b", "c"]}
        assert rep["en"]["missing"] == []

    def test_depth_chrome_covers_nine_languages(self):
        s = build_depth_surface()
        rep = s.coverage()
        assert set(rep) == {"en", "pt", "fr", "pcm", "ha", "yo", "ig",
                            "sw", "ar"}
        assert all(v["missing"] == [] for v in rep.values())

    def test_depth_chrome_uses_registered_languages_only(self):
        for strings in DEPTH_CHROME.values():
            assert set(strings) <= set(LANGUAGES)


class TestArtifact:
    def test_emit_json_is_deterministic(self):
        a = build_depth_surface().emit_json()
        b = build_depth_surface().emit_json()
        assert json.dumps(a, sort_keys=True) == \
            json.dumps(b, sort_keys=True)
        assert a["languages"]["ar"]["direction"] == "rtl"

    def test_audit_lists_rtl_languages(self):
        rep = audit([build_depth_surface()])
        assert rep["rtl"] == ["ar"]
        assert rep["languages"] == len(LANGUAGES)
        assert len(rep["sha256"]) == 64

    def test_version_stamp(self):
        assert VERSION == "langpack@1.0.0"
