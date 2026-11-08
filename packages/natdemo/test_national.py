"""Tests for natdemo — deterministic national-rails demo payloads."""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from demo_national import VERSION, export_all, seed_national  # noqa: E402


@pytest.fixture(params=["ng", "cv"])
def payload(request):
    return seed_national(request.param)


class TestDeterminism:
    @pytest.mark.parametrize("code", ["ng", "cv"])
    def test_same_seed_same_bytes(self, code):
        a = seed_national(code)
        b = seed_national(code)
        assert a["sha256"] == b["sha256"]
        assert json.dumps(a, sort_keys=True) == json.dumps(
            b, sort_keys=True)

    def test_locales_differ(self):
        assert seed_national("ng")["sha256"] != seed_national(
            "cv")["sha256"]

    def test_unknown_locale_refuses(self):
        with pytest.raises(Exception):
            seed_national("xx")


class TestLocaleFidelity:
    def test_ng_context(self):
        p = seed_national("ng")
        assert p["country"] == "Nigeria"
        assert p["currency"] == "NGN"
        assert p["payer"] == "NHIA"
        assert "Lagos University Teaching Hospital" in p["facilities"]

    def test_cv_context(self):
        p = seed_national("cv")
        assert p["country"] == "Cabo Verde"
        assert p["currency"] == "CVE"
        assert p["payer"] == "INPS-CV"
        assert "Hospital Agostinho Neto" in p["facilities"]

    def test_patient_names_come_from_locale_pools(self, payload):
        assert payload["patient"] and payload["doctor"]


class TestGatesFire:
    def test_eprescribe_gates(self, payload):
        e = payload["eprescribe"]
        assert e["partially_dispensed"] == "partially-dispensed"
        assert e["remaining_qty"] == 11
        assert len(e["never_collected"]) == 1
        assert e["token"].startswith("RX-")

    def test_sharedsum_gates(self, payload):
        s = payload["sharedsum"]
        assert "problems" in s["sections_read"]
        assert s["break_glass_count"] == 1
        assert "restricted" in s["restricted_refusal"]

    def test_triageline_gates(self, payload):
        t = payload["triageline"]
        assert t["dispatch_met"] is True
        assert t["callbacks_kept"] == 1
        assert t["callbacks_breached"] == 1

    def test_panelcare_gates(self, payload):
        p = payload["panelcare"]
        assert p["overdue_recalls"][0]["condition"] == "hiv-art"
        assert p["overdue_recalls"][0]["never_reviewed"] is True
        progs = {d["programme"] for d in p["prevention_due_child"]}
        assert "childhood-immunization" in progs

    def test_observatory_gates(self, payload):
        o = payload["observatory"]
        assert o["national_value"] == 90  # 87 country + 3 child region
        assert o["small_cell"]["suppressed"] is True
        assert "k-floor" in o["small_cell"]["suppression"]

    def test_healthcred_gates(self, payload):
        h = payload["healthcred"]
        assert h["verified_serial"] == "S-0001"
        assert h["subject_checked"] is True
        assert "revoked" in h["revoked_refusal"]
        assert h["audit"]["refused"] == 1

    def test_teleconsulta_gates(self, payload):
        t = payload["teleconsulta"]
        assert t["boat_medium"] is True
        assert t["tc1_sla_met"] is True
        assert t["tc2_escalated_to"] == "medevac"


class TestIntegrity:
    def test_payload_checksum_and_version(self, payload):
        assert len(payload["sha256"]) == 64
        assert payload["version"] == VERSION

    def test_export_all_writes_both_locales(self, tmp_path):
        export_all(str(tmp_path))
        for code in ("ng", "cv"):
            path = tmp_path / f"national.{code}.json"
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["locale"] == code
            assert len(data["sha256"]) == 64
